const BASE_URL = import.meta.env.VITE_API_URL || "/api";

async function request(path, options = {}) {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json", ...options.headers },
    ...options
  });
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

  speak: (text) => `${BASE_URL}/tts/speak?text=${encodeURIComponent(text)}`
};
