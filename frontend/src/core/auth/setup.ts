import { AUTH_REQUEST_TIMEOUT_MS } from "./constants";
import { parseAuthError } from "./types";

export type SetupStatusResponse = {
  needs_setup?: boolean;
  registration_enabled?: boolean;
};

export const setupStatusFetchInit = {
  cache: "no-store",
  credentials: "include",
} satisfies RequestInit;

export async function fetchSetupStatus(): Promise<SetupStatusResponse> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), AUTH_REQUEST_TIMEOUT_MS);

  try {
    const response = await fetch("/api/v1/auth/setup-status", {
      ...setupStatusFetchInit,
      signal: controller.signal,
    });
    if (!response.ok) {
      throw new Error(`setup-status failed: ${response.status}`);
    }
    return (await response.json()) as SetupStatusResponse;
  } finally {
    clearTimeout(timeout);
  }
}

export function isSystemAlreadyInitializedError(data: unknown): boolean {
  return parseAuthError(data).code === "system_already_initialized";
}

export function canCreateRegularAccount(
  status: SetupStatusResponse | null,
): boolean {
  // registration_enabled is absent on older Gateways; treat that as allowed so
  // the signup entry only disappears when the backend actively closes it.
  return status?.needs_setup !== true && status?.registration_enabled !== false;
}
