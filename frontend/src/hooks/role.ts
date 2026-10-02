import { createContext, useContext } from "react";

/** Role of the signed-in user: "admin" (everything) or "viewer" (read-only). */
export const RoleContext = createContext<string>("admin");

/** False for read-only accounts: edit controls are hidden (the API refuses changes anyway). */
export const useCanEdit = () => useContext(RoleContext) === "admin";
