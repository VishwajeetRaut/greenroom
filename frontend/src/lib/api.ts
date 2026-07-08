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
  constraints: string[];
  examples: Record<string, unknown>[];
  is_stdio: boolean;
}

export interface SendMessageResponse { question: string; done?: boolean; question_context?: QuestionContext }

export interface RunCodePayload { language: string; version: string; source: string; stdin?: string }
export interface CodeJobResult { run: { stdout: string; stderr: string; code: number } }

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

  runCode: async (payload: RunCodePayload): Promise<CodeJobResult | null> => {
    const { job_id } = await request<{ job_id: string }>("/interview/code/run", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    const deadline = Date.now() + 90_000;
    while (Date.now() < deadline) {
      await new Promise<void>((r) => setTimeout(r, 1000));
      const job = await request<{ status: string; result: CodeJobResult | null }>(
        `/interview/code/job/${job_id}`
      );
      if (job.status === "done" || job.status === "error") return job.result;
    }
    throw new Error("Code execution timed out after 90 seconds.");
  },

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
};
