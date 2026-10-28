#!/usr/bin/env bash
# Builds the VMware appliance (OVA) from the offline installation package:
#
#   scripts/build-package.sh                                   # -> dist/sflow-analytics-<v>.tar.gz
#   appliance/build-ova.sh dist/sflow-analytics-<v>.tar.gz        # -> dist/sflow-analytics-<v>.ova (normal user)
#
# Base: official Ubuntu 24.04 LTS cloud image (checksum verified), customized offline with
# virt-customize (libguestfs-tools, qemu-utils). The application is installed at first boot by
# the console assistant (appliance/files/usr/local/sbin/sflow-setup), on the second (data) disk.
# Virtual hardware: 4 vCPU, 8 GB, PVSCSI, VMXNET3, BIOS, hardware version 14 (ESXi 6.7+);
# disk 1 (system) 30 GB and disk 2 (data) 100 GB, both thin.
set -euo pipefail
cd "$(dirname "$0")/.."
PKG="${1:?usage: build-ova.sh dist/sflow-analytics-<version>.tar.gz}"
NAME="$(basename "$PKG" .tar.gz)"            # sflow-analytics-<version>
VERSION="${NAME#sflow-analytics-}"
WORK="${WORK:-/data/ova-build}"
OUT="${OUT:-dist}"
CPUS="${CPUS:-4}" MEM_MB="${MEM_MB:-8192}" SYS_GB="${SYS_GB:-30}" DATA_GB="${DATA_GB:-100}"
BASE_URL="https://cloud-images.ubuntu.com/releases/noble/release"
BASE_IMG="ubuntu-24.04-server-cloudimg-amd64.img"
PACKAGES="docker.io docker-compose-v2 open-vm-tools whiptail cloud-guest-utils curl"
# The image is customized offline (the libguestfs appliance has no network here): the packages
# are downloaded beforehand in a throw-away ubuntu:24.04 container and installed from files.
export LIBGUESTFS_BACKEND=direct
command -v docker >/dev/null || { echo "docker is required (package download)" >&2; exit 1; }

mkdir -p "$WORK" "$OUT"
echo "==> Base image"
# Ubuntu republishes the release image from time to time: a cached copy whose checksum no longer
# matches SHA256SUMS is downloaded again (once).
SUMS="$(curl -fsSL "$BASE_URL/SHA256SUMS" | grep " \*\?$BASE_IMG\$" | sed "s| \*\?$BASE_IMG| $WORK/$BASE_IMG|")"
if [ ! -s "$WORK/$BASE_IMG" ] || ! echo "$SUMS" | sha256sum -c --status -; then
  curl -fsSL -o "$WORK/$BASE_IMG.part" "$BASE_URL/$BASE_IMG"
  mv "$WORK/$BASE_IMG.part" "$WORK/$BASE_IMG"
fi
echo "$SUMS" | sha256sum -c -

echo "==> Packages: $PACKAGES"
mkdir -p "$WORK/debs" && rm -f "$WORK/debs"/*.deb
# Empty dpkg status: the whole dependency closure is downloaded, so that the offline install
# also finds the newer libraries some packages need (the cloud image may be older).
docker run --rm -v "$WORK/debs:/out" ubuntu:24.04 bash -c "apt-get update -qq && touch /tmp/empty && apt-get install -y -qq --download-only --no-install-recommends -o Dir::State::status=/tmp/empty $PACKAGES >/dev/null && cp /var/cache/apt/archives/*.deb /out/ && chmod 644 /out/*.deb"
echo "    $(ls "$WORK/debs" | wc -l) .deb files"

echo "==> Customizing (offline, may take 10-20 min without hardware virtualization)"
rm -rf "$WORK/pkg" "$WORK/ova" && mkdir -p "$WORK/pkg" "$WORK/ova"
tar xzf "$PKG" -C "$WORK/pkg"
cp "$WORK/$BASE_IMG" "$WORK/system.qcow2"
qemu-img resize -q "$WORK/system.qcow2" "${SYS_GB}G"
virt-customize -a "$WORK/system.qcow2" --memsize 2048 --smp 2 --no-logfile --no-network \
  --touch /etc/cloud/cloud-init.disabled \
  --copy-in "$WORK/debs:/tmp" \
  --run-command 'DEBIAN_FRONTEND=noninteractive dpkg -i --skip-same-version /tmp/debs/*.deb >/tmp/dpkg.log 2>&1 || { tail -40 /tmp/dpkg.log; exit 1; }; rm -rf /tmp/debs /tmp/dpkg.log' \
  --run-command 'systemctl disable docker.service docker.socket containerd.service' \
  --run-command 'useradd -m -s /bin/bash -G sudo,docker sflow && passwd -l sflow' \
  --copy-in appliance/files/etc:/ \
  --copy-in appliance/files/usr:/ \
  --copy-in "$WORK/pkg/$NAME:/opt" \
  --run-command "mv /opt/$NAME /opt/sflow-package && chmod 755 /opt/sflow-package/*.sh" \
  --run-command 'chmod 755 /usr/local/sbin/sflow-*; chmod 600 /etc/netplan/*.yaml; rm -f /etc/netplan/50-cloud-init.yaml' \
  --run-command 'rm -f /etc/ssh/sshd_config.d/60-cloudimg-settings.conf /etc/ssh/ssh_host_*' \
  --run-command 'systemctl enable sflow-issue.service sflow-setup.service ssh.service open-vm-tools.service' \
  --hostname sflow-analytics --timezone UTC \
  --run-command 'apt-get clean; rm -rf /var/lib/apt/lists/* /var/log/*.log; truncate -s 0 /etc/machine-id; rm -f /var/lib/dbus/machine-id'
virt-sparsify --in-place "$WORK/system.qcow2" >/dev/null

echo "==> VMware disks"
# Both disks are shipped: VirtualBox needs a file for every disk of an OVA ("Unexpected end of
# OVA package" otherwise). The data disk holds only a short text at 1 MiB: a completely empty
# streamOptimized VMDK is rejected by the ESXi NFC upload ("Error on read"), and the text is no
# partition table nor file system, so sflow-setup still sees an empty disk and formats it.
D1="$NAME-disk1.vmdk" D2="$NAME-disk2.vmdk"
qemu-img convert -O vmdk -o subformat=streamOptimized,adapter_type=lsilogic "$WORK/system.qcow2" "$WORK/ova/$D1"
qemu-img create -q -f raw "$WORK/data.raw" "${DATA_GB}G"
printf 'sflow-analytics data disk (formatted at first boot)' | dd of="$WORK/data.raw" bs=1 seek=1048576 conv=notrunc status=none
qemu-img convert -O vmdk -o subformat=streamOptimized,adapter_type=lsilogic "$WORK/data.raw" "$WORK/ova/$D2"
rm -f "$WORK/data.raw"
S1=$(stat -c %s "$WORK/ova/$D1") S2=$(stat -c %s "$WORK/ova/$D2")

echo "==> OVF descriptor"
cat > "$WORK/ova/$NAME.ovf" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<Envelope xmlns="http://schemas.dmtf.org/ovf/envelope/1" xmlns:ovf="http://schemas.dmtf.org/ovf/envelope/1"
  xmlns:rasd="http://schemas.dmtf.org/wbem/wscim/1/cim-schema/2/CIM_ResourceAllocationSettingData"
  xmlns:vssd="http://schemas.dmtf.org/wbem/wscim/1/cim-schema/2/CIM_VirtualSystemSettingData"
  xmlns:vmw="http://www.vmware.com/schema/ovf" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <References>
    <File ovf:id="file1" ovf:href="$D1" ovf:size="$S1"/>
    <File ovf:id="file2" ovf:href="$D2" ovf:size="$S2"/>
  </References>
  <DiskSection>
    <Info>Virtual disks</Info>
    <Disk ovf:diskId="system" ovf:fileRef="file1" ovf:capacity="$SYS_GB" ovf:capacityAllocationUnits="byte * 2^30"
      ovf:format="http://www.vmware.com/interfaces/specifications/vmdk.html#streamOptimized"/>
    <Disk ovf:diskId="data" ovf:fileRef="file2" ovf:capacity="$DATA_GB" ovf:capacityAllocationUnits="byte * 2^30"
      ovf:format="http://www.vmware.com/interfaces/specifications/vmdk.html#streamOptimized"/>
  </DiskSection>
  <NetworkSection>
    <Info>Networks</Info>
    <Network ovf:name="VM Network"><Description>Network of the switches and of the users</Description></Network>
  </NetworkSection>
  <VirtualSystem ovf:id="$NAME">
    <Info>sFlow Analytics $VERSION appliance</Info>
    <Name>sflow-analytics</Name>
    <AnnotationSection>
      <Info>Description</Info>
      <Annotation>sFlow collector and traffic analytics for RUCKUS ICX switches. Open the VM console at first boot: a setup assistant configures the network and installs the application. Web interface on https, sFlow on UDP 6343.</Annotation>
    </AnnotationSection>
    <ProductSection>
      <Info>Product</Info>
      <Product>sFlow Analytics</Product>
      <Version>$VERSION</Version>
      <FullVersion>$VERSION</FullVersion>
    </ProductSection>
    <OperatingSystemSection ovf:id="94" vmw:osType="ubuntu64Guest">
      <Info>Ubuntu 24.04 LTS (64-bit)</Info>
    </OperatingSystemSection>
    <VirtualHardwareSection>
      <Info>Virtual hardware</Info>
      <System>
        <vssd:ElementName>Virtual Hardware Family</vssd:ElementName>
        <vssd:InstanceID>0</vssd:InstanceID>
        <vssd:VirtualSystemIdentifier>sflow-analytics</vssd:VirtualSystemIdentifier>
        <vssd:VirtualSystemType>vmx-14</vssd:VirtualSystemType>
      </System>
      <Item>
        <rasd:AllocationUnits>hertz * 10^6</rasd:AllocationUnits>
        <rasd:Description>Number of virtual CPUs</rasd:Description>
        <rasd:ElementName>$CPUS virtual CPU(s)</rasd:ElementName>
        <rasd:InstanceID>1</rasd:InstanceID>
        <rasd:ResourceType>3</rasd:ResourceType>
        <rasd:VirtualQuantity>$CPUS</rasd:VirtualQuantity>
      </Item>
      <Item>
        <rasd:AllocationUnits>byte * 2^20</rasd:AllocationUnits>
        <rasd:Description>Memory size</rasd:Description>
        <rasd:ElementName>$MEM_MB MB of memory</rasd:ElementName>
        <rasd:InstanceID>2</rasd:InstanceID>
        <rasd:ResourceType>4</rasd:ResourceType>
        <rasd:VirtualQuantity>$MEM_MB</rasd:VirtualQuantity>
      </Item>
      <Item>
        <rasd:Address>0</rasd:Address>
        <rasd:Description>SCSI controller</rasd:Description>
        <rasd:ElementName>SCSI controller 0</rasd:ElementName>
        <rasd:InstanceID>3</rasd:InstanceID>
        <rasd:ResourceSubType>VirtualSCSI</rasd:ResourceSubType>
        <rasd:ResourceType>6</rasd:ResourceType>
      </Item>
      <Item>
        <rasd:AddressOnParent>0</rasd:AddressOnParent>
        <rasd:ElementName>Hard disk 1 (system)</rasd:ElementName>
        <rasd:HostResource>ovf:/disk/system</rasd:HostResource>
        <rasd:InstanceID>4</rasd:InstanceID>
        <rasd:Parent>3</rasd:Parent>
        <rasd:ResourceType>17</rasd:ResourceType>
      </Item>
      <Item>
        <rasd:AddressOnParent>1</rasd:AddressOnParent>
        <rasd:ElementName>Hard disk 2 (data)</rasd:ElementName>
        <rasd:HostResource>ovf:/disk/data</rasd:HostResource>
        <rasd:InstanceID>5</rasd:InstanceID>
        <rasd:Parent>3</rasd:Parent>
        <rasd:ResourceType>17</rasd:ResourceType>
      </Item>
      <Item>
        <rasd:AddressOnParent>7</rasd:AddressOnParent>
        <rasd:AutomaticAllocation>true</rasd:AutomaticAllocation>
        <rasd:Connection>VM Network</rasd:Connection>
        <rasd:ElementName>Network adapter 1</rasd:ElementName>
        <rasd:InstanceID>6</rasd:InstanceID>
        <rasd:ResourceSubType>VmxNet3</rasd:ResourceSubType>
        <rasd:ResourceType>10</rasd:ResourceType>
      </Item>
      <Item ovf:required="false">
        <rasd:AutomaticAllocation>false</rasd:AutomaticAllocation>
        <rasd:ElementName>Video card</rasd:ElementName>
        <rasd:InstanceID>7</rasd:InstanceID>
        <rasd:ResourceType>24</rasd:ResourceType>
      </Item>
      <vmw:Config ovf:required="false" vmw:key="tools.syncTimeWithHost" vmw:value="false"/>
      <vmw:Config ovf:required="false" vmw:key="firmware" vmw:value="bios"/>
    </VirtualHardwareSection>
  </VirtualSystem>
</Envelope>
EOF

echo "==> OVA"
(cd "$WORK/ova" && for f in "$NAME.ovf" "$D1" "$D2"; do echo "SHA256($f)= $(sha256sum "$f" | cut -d' ' -f1)"; done > "$NAME.mf")
tar -C "$WORK/ova" --format=ustar -cf "$OUT/$NAME.ova" "$NAME.ovf" "$NAME.mf" "$D1" "$D2"
(cd "$OUT" && sha256sum "$NAME.ova" > "$NAME.ova.sha256")
echo "==> $OUT/$NAME.ova ($(du -h "$OUT/$NAME.ova" | cut -f1))"
