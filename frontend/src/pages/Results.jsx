import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import Navbar from "../components/Navbar";
import Footer from "../components/Footer";
import { supabase } from "../lib/supabaseClient";
import Losgann from "../components/Losgann";
import SessionReplay from "../components/SessionReplay";

export default function Results() {
  const { sessionId } = useParams();
  const [session, setSession] = useState(null);
  const [evaluations, setEvaluations] = useState([]);
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(true);
  const [losgannDismissed, setLosgannDismissed] = useState(false);

  useEffect(() => {
    let mounted = true;

    async function load() {
      const [{ data: sessionData }, { data: evalData }, { data: messageData }] = await Promise.all([
        supabase.from("sessions").select("*").eq("id", sessionId).single(),
        supabase.from("evaluations").select("*").eq("session_id", sessionId),
        supabase
          .from("messages")
          .select("*")
          .eq("session_id", sessionId)
          .order("created_at", { ascending: true })
      ]);

      if (!mounted) return;
      setSession(sessionData ?? null);
      setEvaluations(evalData ?? []);
      setMessages(messageData ?? []);
      setLoading(false);
    }

    load();
    return () => {
      mounted = false;
    };
  }, [sessionId]);

  return (
    <div className="flex min-h-screen flex-col">
      <Navbar />
      <main className="flex-1">
        <section className="mx-auto max-w-4xl px-6 py-12">
          <Link to="/dashboard" className="text-sm text-mute hover:text-cream">
            &larr; Back to dashboard
          </Link>

          {loading ? (
            <p className="mt-6 text-sm text-mute">Loading your report...</p>
          ) : !session ? (
            <p className="mt-6 text-sm text-mute">We couldn't find this session.</p>
          ) : (
            <>
              <div className="mt-4 flex items-center justify-between">
                <div>
                  <p className="text-sm text-mute capitalize">{session.track} session</p>
                  <h1 className="mt-1 font-display text-4xl tracking-tight">Session report</h1>
                </div>
                {session.overall_score != null && session.overall_score > 0 && (
                  <div className="text-right">
                    <p className="font-display text-5xl text-amber">{session.overall_score}</p>
                    <p className="text-xs text-mute">out of 10</p>
                  </div>
                )}
              </div>

              {session.summary && (
                <div className="mt-8 rounded-2xl border border-white/10 bg-panel p-6">
                  <h2 className="font-display text-xl">Summary</h2>
                  <p className="mt-3 text-sm leading-relaxed text-mute">{session.summary}</p>
                </div>
              )}

              {evaluations.length > 0 && (
                <div className="mt-6 grid gap-4 sm:grid-cols-2">
                  {evaluations.map((e) => (
                    <div key={e.id} className="rounded-2xl border border-white/10 bg-panel p-6">
                      <div className="flex items-center justify-between">
                        <h3 className="font-display text-lg capitalize">{e.category}</h3>
                        <span className="font-display text-2xl text-sage">{e.score}/10</span>
                      </div>
                      <p className="mt-2 text-sm text-mute">{e.feedback}</p>
                    </div>
                  ))}
                </div>
              )}

              {session.star_analysis && (() => {
                const star = typeof session.star_analysis === "string"
                  ? JSON.parse(session.star_analysis)
                  : session.star_analysis;
                const elements = ["situation", "task", "action", "result"];
                return (
                  <div className="mt-6 rounded-2xl border border-white/10 bg-panel p-6">
                    <div className="flex items-center justify-between">
                      <h2 className="font-display text-xl">STAR Analysis</h2>
                      <span className="font-display text-2xl text-amber">{star.star_score}/10</span>
                    </div>
                    <div className="mt-4 grid gap-3 sm:grid-cols-2">
                      {elements.map((el) => (
                        <div key={el} className="rounded-xl border border-white/5 bg-panelLight/40 p-4">
                          <p className="text-xs font-medium uppercase tracking-wide text-mute">{el}</p>
                          <p className="mt-1 text-sm text-cream/90">{star[el]}</p>
                        </div>
                      ))}
                    </div>
                    {star.missing_elements?.length > 0 && (
                      <div className={`mt-4 transition-opacity duration-500 ${losgannDismissed ? "opacity-100" : "opacity-0 pointer-events-none"}`}>
                        <p className="text-xs font-medium uppercase tracking-wide text-coral">Missing / vague</p>
                        <ul className="mt-2 list-inside list-disc space-y-1 text-sm text-mute">
                          {star.missing_elements.map((m, i) => <li key={i}>{m}</li>)}
                        </ul>
                      </div>
                    )}
                    <Losgann missingElements={star.missing_elements} onDismiss={() => setLosgannDismissed(true)} />
                  </div>
                );
              })()}

              <SessionReplay messages={messages} evaluations={evaluations} />

              <div className="mt-10">
  <div className="flex items-center justify-between">
    <h2 className="font-display text-xl">Transcript</h2>
    <button
      onClick={() => window.print()}
      className="flex items-center gap-2 rounded-full border border-white/10 px-4 py-2 text-xs text-mute transition hover:border-white/30 hover:text-cream"
    >
      🖨 Print transcript
    </button>
  </div>
                <div className="mt-4 space-y-3">
                  {messages.map((m) => (
                    <div
                      key={m.id}
                      className={
                        m.role === "interviewer"
                          ? "rounded-xl bg-panel p-4 text-sm"
                          : "rounded-xl border border-amber/20 bg-amber/5 p-4 text-sm"
                      }
                    >
                      <p className={`font-display ${m.role === "interviewer" ? "text-cream" : "text-amber"}`}>
                        {m.role === "interviewer" ? "Interviewer" : "You"}
                      </p>
                      <p className="mt-1 text-cream/90">{m.content}</p>
                    </div>
                  ))}
                  {messages.length === 0 && (
                    <p className="text-sm text-mute">No transcript was saved for this session.</p>
                  )}
                </div>
              </div>

              <div className="mt-10 flex gap-4">
                <Link
                  to={`/interview?track=${session.track}`}
                  className="rounded-full bg-amber px-6 py-3 text-sm font-medium text-ink transition hover:bg-amberDark"
                >
                  Practice again
                </Link>
                <Link
                  to="/dashboard"
                  className="rounded-full border border-white/10 px-6 py-3 text-sm text-cream transition hover:border-white/25"
                >
                  Back to dashboard
                </Link>
              </div>
            </>
          )}
        </section>
      </main>
      <Footer />
    </div>
  );
}