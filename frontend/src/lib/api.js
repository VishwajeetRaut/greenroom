import { supabase } from "./supabaseClient";

const BASE_URL = import.meta.env.VITE_API_URL || "/api";

async function request(path, options = {}) {
  const { data } = await supabase.auth.getSession();
  const token = data?.session?.access_token;

  const headers = { "Content-Type": "application/json", ...options.headers };
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(`${BASE_URL}${path}`, { headers, ...options });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`API error ${res.status}: ${text}`);
  }
  return res.json();
}

export const api = {
  startSession: (payload) =>
    request("/interview/start", { method: "POST", body: JSON.stringify(payload) }),

  sendMessage: (payload) =>
    request("/interview/message", { method: "POST", body: JSON.stringify(payload) }),

  runCode: (payload) =>
    request("/interview/code/run", { method: "POST", body: JSON.stringify(payload) }),

  runTests: (payload) =>
    request("/interview/code/test", { method: "POST", body: JSON.stringify(payload) }),

  endSession: (payload) =>
    request("/interview/end", { method: "POST", body: JSON.stringify(payload) }),

  deleteSession: (sessionId) =>
    request(`/interview/${sessionId}`, { method: "DELETE" }),

  getBoilerplate: (sessionId, language) =>
    request(`/interview/${sessionId}/boilerplate?language=${encodeURIComponent(language)}`),

  speak: (text) => `${BASE_URL}/tts/speak?text=${encodeURIComponent(text)}`
};
