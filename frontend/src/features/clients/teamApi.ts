/**
 * A client's team (who may work on it, and as what). All helpers reject with `ApiError`.
 */
import { type components, getApiClient } from "@/lib/api";

export type Member = components["schemas"]["Member"];
export type TeamRole = components["schemas"]["TeamRoleEnum"];

export const ROLES: ReadonlyArray<{
  value: TeamRole;
  label: string;
  hint: string;
}> = [
  { value: "viewer", label: "Viewer", hint: "Can look, cannot decide." },
  { value: "reviewer", label: "Reviewer", hint: "Can decide ADD, HOLD, SKIP." },
  { value: "manager", label: "Manager", hint: "Can also edit and analyze." },
  { value: "admin", label: "Admin", hint: "Can also manage the team." },
];

export async function getMembers(
  clientId: string,
  signal?: AbortSignal,
): Promise<Member[]> {
  const { data } = await getApiClient().GET("/api/v1/clients/{id}/members/", {
    params: { path: { id: clientId } },
    signal,
  });
  return data as Member[];
}

export async function addMember(
  clientId: string,
  input: { email: string; role: TeamRole; name?: string; password?: string },
): Promise<Member> {
  const { data } = await getApiClient().POST(
    "/api/v1/clients/{id}/add-member/",
    {
      params: { path: { id: clientId } },
      body: {
        email: input.email,
        role: input.role,
        name: input.name ?? "",
        ...(input.password ? { password: input.password } : {}),
      },
    },
  );
  return data as Member;
}

export async function setMemberRole(
  clientId: string,
  userId: string,
  role: TeamRole,
): Promise<Member> {
  const { data } = await getApiClient().POST(
    "/api/v1/clients/{id}/set-member-role/",
    { params: { path: { id: clientId } }, body: { user_id: userId, role } },
  );
  return data as Member;
}

export async function removeMember(
  clientId: string,
  userId: string,
): Promise<void> {
  await getApiClient().POST("/api/v1/clients/{id}/remove-member/", {
    params: { path: { id: clientId } },
    body: { user_id: userId },
  });
}

export async function changePassword(
  current: string,
  next: string,
): Promise<void> {
  await getApiClient().POST("/api/v1/auth/change-password/", {
    body: { current_password: current, new_password: next },
  });
}
