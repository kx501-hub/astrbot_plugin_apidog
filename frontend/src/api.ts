interface PageBridge {
  ready(): Promise<unknown>;
  apiGet<T>(endpoint: string): Promise<T>;
  apiPost<T>(endpoint: string, body: unknown): Promise<T>;
}

declare global {
  interface Window { AstrBotPluginPage?: PageBridge; }
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const bridge = window.AstrBotPluginPage;
  if (!bridge) throw new Error("请从 AstrBot 插件页面打开 ApiDog 管理台");
  await bridge.ready();
  const endpoint = path.replace(/^\//, "");
  if (options?.method === "POST") {
    return bridge.apiPost<T>(endpoint, JSON.parse(String(options.body)));
  }
  return bridge.apiGet<T>(endpoint);
}

export async function getConfig() {
  return request<Record<string, unknown>>("/config");
}
export async function putConfig(body: Record<string, unknown>) {
  return request<{ saved: boolean }>("/config/save", { method: "POST", body: JSON.stringify(body) });
}

export async function getApis() {
  return request<Record<string, unknown>[]>("/apis");
}
export async function putApis(apis: Record<string, unknown>[]) {
  return request<{ saved: boolean }>("/apis/save", { method: "POST", body: JSON.stringify({ apis }) });
}

export async function getSchedules() {
  return request<Record<string, unknown>[]>("/schedules");
}
export async function putSchedules(schedules: Record<string, unknown>[]) {
  return request<{ saved: boolean }>("/schedules/save", { method: "POST", body: JSON.stringify({ schedules }) });
}

export async function getGroups() {
  return request<{ user_groups?: Record<string, string[]>; group_groups?: Record<string, string[]> }>("/groups");
}
export async function putGroups(body: Record<string, unknown>) {
  return request<{ saved: boolean }>("/groups/save", { method: "POST", body: JSON.stringify(body) });
}

export async function getAuth() {
  return request<Record<string, unknown>>("/auth");
}
export async function putAuth(body: Record<string, unknown>) {
  return request<{ saved: boolean }>("/auth/save", { method: "POST", body: JSON.stringify(body) });
}
