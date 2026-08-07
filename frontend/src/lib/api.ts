import { supabase } from "./supabaseClient";

const BASE_URL: string = import.meta.env.VITE_API_URL || "/api";

interface RequestOptions extends RequestInit {
  headers?: Record<string, string>;
}

async function request<T = unknown>(path: string, options: RequestOptions = {}): Promise<T> {
  const { data } = await supabase.auth.getSession();
  const token = data?.session?.access_token;

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...options.headers,
  };
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(`${BASE_URL}${path}`, { ...options, headers });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`API error ${res.status}: ${text}`);
  }
  return res.json() as Promise<T>;
}

// ── Request / response shapes ─────────────────────────────────────────────────

export interface StartSessionPayload { track: string; role?: string; job_description?: string }
export interface StartSessionResponse { session_id: string; track: string; question: string }

export interface SendMessagePayload { session_id: string; message: string; code?: string; language?: string }

export interface QuestionContext {
  id: string;
  title: string;
  difficulty: string;
  prompt: string;
  constraints: string[];
  examples: Record<string, unknown>[];
  is_stdio: boolean;
  // System-design only — empty for the other tracks.
  tags?: string[];
  core_challenge?: string | null;
  scale?: string[];
}

export interface SendMessageResponse { question: string; done?: boolean; question_context?: QuestionContext }

export interface RunTestsPayload { session_id: string; language: string; version: string; source: string }
export interface TestResult { id: number; label: string; input: string; expected: string; output?: string; error?: string; passed: boolean }
export interface HiddenTestResult { id: number; passed: boolean }
export interface RunTestsResponse {
  status: string;
  visible_tests: TestResult[];
  hidden_tests: HiddenTestResult[];
  passed: number;
  total: number;
  compile_error?: string;
  error_type?: "transient" | "permanent";
}

export interface EndSessionPayload { session_id: string }
export interface EvaluationCategory { category: string; score: number; feedback: string }
export interface DiagramEvaluation {
  components_found: string[];
  components_missing: string[];
  proximity_score: number;
  proximity_label: "needs work" | "reasonable" | "strong";
  feedback: string;
}
export interface EndSessionResponse {
  overall_score: number;
  summary: string;
  star_analysis?: object;
  evaluations: EvaluationCategory[];
  diagram_evaluation?: DiagramEvaluation;
}

export interface BoilerplateResponse { boilerplate: string | null; supported: boolean }

export interface HistoryMessage { role: string; content: string }
export interface ResumeSessionResponse {
  session_id: string;
  track: string;
  history: HistoryMessage[];
  question_context?: QuestionContext;
  diagram_elements?: unknown[];
}

export interface SaveDiagramPayload { session_id: string; elements: unknown[] }

export interface SessionSummary {
  id: string;
  track: string;
  role?: string | null;
  overall_score?: number | null;
  status: string;
  created_at: string;
}

// ── API client ────────────────────────────────────────────────────────────────

export const api = {
  startSession: (payload: StartSessionPayload) =>
    request<StartSessionResponse>("/interview/start", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  sendMessage: (payload: SendMessagePayload) =>
    request<SendMessageResponse>("/interview/message", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  runTests: (payload: RunTestsPayload) =>
    request<RunTestsResponse>("/interview/code/test", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  endSession: (payload: EndSessionPayload) =>
    request<EndSessionResponse>("/interview/end", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  deleteSession: (sessionId: string) =>
    request<{ deleted: string }>(`/interview/${sessionId}`, { method: "DELETE" }),

  resumeSession: (sessionId: string) =>
    request<ResumeSessionResponse>(`/interview/${sessionId}/resume`),

  listSessions: (opts: { limit?: number; offset?: number } = {}) => {
    const params = new URLSearchParams();
    if (opts.limit != null) params.set("limit", String(opts.limit));
    if (opts.offset != null) params.set("offset", String(opts.offset));
    const qs = params.toString();
    return request<SessionSummary[]>(`/interview/sessions${qs ? `?${qs}` : ""}`);
  },

  saveDiagram: (payload: SaveDiagramPayload) =>
    request<{ saved: boolean }>("/interview/diagram", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  getBoilerplate: (sessionId: string, language: string) =>
    request<BoilerplateResponse>(
      `/interview/${sessionId}/boilerplate?language=${encodeURIComponent(language)}`
    ),

  speak: async (text: string): Promise<string> => {
    const { data } = await supabase.auth.getSession();
    const token = data?.session?.access_token;
    const headers: Record<string, string> = {};
    if (token) headers["Authorization"] = `Bearer ${token}`;

    const res = await fetch(`${BASE_URL}/tts/speak?text=${encodeURIComponent(text)}`, { headers });
    if (!res.ok) {
      const errText = await res.text();
      throw new Error(`API error ${res.status}: ${errText}`);
    }
    const blob = await res.blob();
    return URL.createObjectURL(blob);
  },

  getStats: () =>
    request<Record<string, unknown>>("/analytics/stats"),

  // Fire-and-forget usage/click tracking — never throws, so callers can
  // invoke it inline without try/catch.
  trackEvent: (event: string, opts: { sessionId?: string; properties?: Record<string, unknown> } = {}) => {
    request("/analytics/event", {
      method: "POST",
      body: JSON.stringify({ event, session_id: opts.sessionId, properties: opts.properties }),
    }).catch(() => {});
  },
};
