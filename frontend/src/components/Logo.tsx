/** Logo: sampled flows converging on the collector (same drawing as docs/logo.svg and the favicon). */
export function LogoMark({ size = 26 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" aria-hidden="true" style={{ flex: `0 0 ${size}px` }}>
      <rect width="64" height="64" rx="14" fill="#f58220" />
      <g fill="none" stroke="#fff" strokeWidth="4" strokeLinecap="round">
        <path d="M14 18C30 18 32 32 50 32" /><path d="M14 32H50" /><path d="M14 46C30 46 32 32 50 32" />
      </g>
      <g fill="#fff"><circle cx="14" cy="18" r="4.5" /><circle cx="14" cy="32" r="4.5" /><circle cx="14" cy="46" r="4.5" /><circle cx="50" cy="32" r="6" /></g>
    </svg>
  );
}

const SWITCHES = [50, 137, 224];
const FLOW = (y: number) => `M96 ${y + 13} C150 ${y + 13} 150 150 196 150`;
const BARS = [34, 52, 40, 70, 58, 86, 64];

/** Sign-in illustration: switches send sFlow samples to the collector, which turns them into traffic charts. */
export function LoginArt() {
  const still = typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  return (
    <svg viewBox="0 0 360 300" width="100%" role="img" aria-label="Switches sending sFlow samples to the collector">
      {SWITCHES.map((y, i) => (
        <g key={y}>
          <path id={`flow-${i}`} d={FLOW(y)} fill="none" stroke="#f58220" strokeOpacity="0.35" strokeWidth="2" />
          <rect x="16" y={y} width="80" height="26" rx="4" fill="#2a2f34" stroke="#3d444b" />
          {[0, 1, 2, 3, 4, 5].map((p) => (
            <rect key={p} x={24 + p * 11} y={y + 9} width="7" height="8" rx="1" fill={p === i + 1 ? "#f58220" : "#4f8a5b"} />
          ))}
          {!still && [0, 1, 2].map((k) => (
            <circle key={k} r="3" fill="#ffb066">
              <animateMotion dur="2.4s" repeatCount="indefinite" begin={`${k * 0.8 + i * 0.3}s`}>
                <mpath href={`#flow-${i}`} />
              </animateMotion>
            </circle>
          ))}
        </g>
      ))}
      <circle cx="210" cy="150" r="24" fill="#f58220" />
      <g transform="translate(194 134) scale(0.5)" fill="none" stroke="#fff" strokeWidth="5" strokeLinecap="round">
        <path d="M14 18C30 18 32 32 50 32" /><path d="M14 32H50" /><path d="M14 46C30 46 32 32 50 32" />
      </g>
      <path d="M234 150H252" stroke="#f58220" strokeOpacity="0.6" strokeWidth="2" />
      <rect x="252" y="92" width="94" height="116" rx="6" fill="#2a2f34" stroke="#3d444b" />
      {BARS.map((h, i) => (
        <rect key={i} x={262 + i * 11.5} y={196 - h} width="8" height={h} rx="1.5" fill={i === BARS.length - 2 ? "#f58220" : "#5b636b"} />
      ))}
      <polyline points={BARS.map((h, i) => `${266 + i * 11.5},${190 - h * 0.9}`).join(" ")} fill="none" stroke="#ffb066" strokeWidth="1.5" />
      <text x="16" y="282" fill="#b9c0c7" fontSize="13">Who saturates this uplink? Every flow, every port.</text>
    </svg>
  );
}
