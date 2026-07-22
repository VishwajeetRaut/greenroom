import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import Navbar from "../components/Navbar";
import Footer from "../components/Footer";
import { supabase } from "../lib/supabaseClient";
import { api } from "../lib/api";

const TRACKS = [
  {
    id: "behavioral",
    name: "Behavioral",
    description: "Practice STAR-method answers to common behavioral questions.",
    accent: "bg-amber/15 text-amber"
  },
  {
    id: "technical",
    name: "Technical",
    description: "Talk through a coding problem with a live editor and execution.",
    accent: "bg-sage/15 text-sage"
  },
  {
    id: "system-design",
    name: "System design",
    description: "Reason out loud about architecture, trade-offs, and scale.",
    accent: "bg-coral/15 text-coral"
  }
];

export default function Dashboard() {
  const navigate = useNavigate();
  const [sessions, setSessions] = useState([]);
  const [selectedTrack, setSelectedTrack] = useState(null);
  const [jobDescription, setJobDescription] = useState("");
  const jdRef = useRef(null);

  useEffect(() => {
    if (selectedTrack) jdRef.current?.focus();
  }, [selectedTrack]);

  const handleStartSession = () => {
    if (jobDescription.trim()) sessionStorage.setItem("interview_jd", jobDescription.trim());
    else sessionStorage.removeItem("interview_jd");
    navigate(`/interview?track=${selectedTrack}`);
  };
  const [loading, setLoading] = useState(true);
  const [userEmail, setUserEmail] = useState("");
  const [deletingId, setDeletingId] = useState(null);
  const [selectionMode, setSelectionMode] = useState(false);
  const [selectedIds, setSelectedIds] = useState(new Set());
  const [deletingBulk, setDeletingBulk] = useState(false);

  useEffect(() => {
    let mounted = true;

    async function load() {
      const { data: userData } = await supabase.auth.getUser();
      if (mounted) setUserEmail(userData?.user?.email ?? "");

      const userId = userData?.user?.id;
      if (!userId) {
        if (mounted) setLoading(false);
        return;
      }

      const { data, error } = await supabase
        .from("sessions")
        .select("id, track, role, overall_score, created_at, status")
        .eq("user_id", userId)
        .order("created_at", { ascending: false })
        .limit(10);

      if (!error && mounted) setSessions(data ?? []);
      if (mounted) setLoading(false);
    }

    load();
    return () => {
      mounted = false;
    };
  }, []);

  const handleDelete = async (sessionId) => {
    if (!window.confirm("Delete this session and its transcript? This cannot be undone.")) return;
    setDeletingId(sessionId);
    try {
      await api.deleteSession(sessionId);
      setSessions((prev) => prev.filter((s) => s.id !== sessionId));
    } catch {
      alert("Failed to delete session. Please try again.");
    } finally {
      setDeletingId(null);
    }
  };

  const toggleSelection = (id) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  const toggleSelectAll = () => {
    if (selectedIds.size === sessions.length) setSelectedIds(new Set());
    else setSelectedIds(new Set(sessions.map((s) => s.id)));
  };

  const exitSelectionMode = () => {
    setSelectionMode(false);
    setSelectedIds(new Set());
  };

  const handleDeleteSelected = async () => {
    if (selectedIds.size === 0) return;
    if (!window.confirm(`Delete ${selectedIds.size} session${selectedIds.size > 1 ? "s" : ""}? This cannot be undone.`)) return;
    setDeletingBulk(true);
    try {
      await Promise.all([...selectedIds].map((id) => api.deleteSession(id)));
      setSessions((prev) => prev.filter((s) => !selectedIds.has(s.id)));
      exitSelectionMode();
    } catch {
      alert("Some sessions could not be deleted. Please try again.");
    } finally {
      setDeletingBulk(false);
    }
  };

  return (
    <div className="flex min-h-screen flex-col">
      <Navbar />
      <main className="flex-1">
        <section className="mx-auto max-w-6xl px-6 py-12">
          <p className="text-sm text-mute">Signed in as {userEmail}</p>
          <h1 className="mt-2 font-display text-4xl tracking-tight">
            Ready for your next session?
          </h1>

          <div className="mt-10 grid gap-6 sm:grid-cols-3">
            {TRACKS.map((track) => (
              <button
                key={track.id}
                onClick={() => { setSelectedTrack(track.id); setJobDescription(""); }}
                className="rounded-2xl border border-white/10 bg-panel p-6 text-left transition hover:border-amber/40"
              >
                <span className={`inline-flex rounded-full px-3 py-1 text-xs font-medium ${track.accent}`}>
                  {track.name}
                </span>
                <p className="mt-4 text-sm text-mute">{track.description}</p>
                <span className="mt-6 inline-block text-sm text-amber">Start session &rarr;</span>
              </button>
            ))}
          </div>

          {selectedTrack && (
            <div
              className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-4"
              onClick={(e) => { if (e.target === e.currentTarget) setSelectedTrack(null); }}
            >
              <div className="w-full max-w-lg rounded-2xl border border-white/10 bg-panel p-6 shadow-xl">
                <div className="flex items-center justify-between">
                  <h2 className="font-display text-xl">
                    {TRACKS.find((t) => t.id === selectedTrack)?.name} interview
                  </h2>
                  <button onClick={() => setSelectedTrack(null)} className="text-mute hover:text-cream">✕</button>
                </div>
                <p className="mt-1 text-sm text-mute">
                  Paste a job description to tailor questions to the role — or leave blank for a general interview.
                </p>
                <textarea
                  ref={jdRef}
                  value={jobDescription}
                  onChange={(e) => setJobDescription(e.target.value)}
                  placeholder="Paste job description here (optional)..."
                  rows={7}
                  maxLength={5000}
                  className="mt-4 w-full resize-none rounded-xl border border-white/10 bg-panelLight/40 px-4 py-3 text-sm text-cream outline-none placeholder:text-mute/50 focus:border-amber/40"
                />
                {jobDescription.length > 0 && (
                  <p className="mt-1 text-right text-xs text-mute">{jobDescription.length} / 5000</p>
                )}
                <div className="mt-4 flex items-center justify-end gap-3">
                  <button
                    onClick={() => setSelectedTrack(null)}
                    className="rounded-full border border-white/10 px-4 py-2 text-sm text-mute transition hover:text-cream"
                  >
                    Cancel
                  </button>
                  <button
                    onClick={handleStartSession}
                    className="rounded-full bg-amber px-5 py-2 text-sm font-medium text-ink transition hover:bg-amberDark"
                  >
                    Start session
                  </button>
                </div>
              </div>
            </div>
          )}

          <div className="mt-16">
            <div className="flex items-center justify-between">
              <h2 className="font-display text-2xl tracking-tight">Recent sessions</h2>
              {!loading && sessions.length > 0 && (
                <div className="flex items-center gap-3">
                  {selectionMode ? (
                    <>
                      <button
                        onClick={toggleSelectAll}
                        className="text-sm text-mute transition hover:text-cream"
                      >
                        {selectedIds.size === sessions.length ? "Deselect all" : "Select all"}
                      </button>
                      <button
                        onClick={handleDeleteSelected}
                        disabled={selectedIds.size === 0 || deletingBulk}
                        className="rounded-full bg-coral/10 px-4 py-1.5 text-sm text-coral transition hover:bg-coral/20 disabled:opacity-40"
                      >
                        {deletingBulk ? "Deleting..." : `Delete selected${selectedIds.size > 0 ? ` (${selectedIds.size})` : ""}`}
                      </button>
                      <button
                        onClick={exitSelectionMode}
                        className="text-sm text-mute transition hover:text-cream"
                      >
                        Cancel
                      </button>
                    </>
                  ) : (
                    <button
                      onClick={() => setSelectionMode(true)}
                      className="rounded-full border border-white/10 px-4 py-1.5 text-sm text-mute transition hover:border-coral/40 hover:text-coral"
                    >
                      Select sessions
                    </button>
                  )}
                </div>
              )}
            </div>

            {loading ? (
              <p className="mt-4 text-sm text-mute">Loading...</p>
            ) : sessions.length === 0 ? (
              <div className="mt-4 rounded-2xl border border-dashed border-white/10 p-8 text-center text-sm text-mute">
                No sessions yet. Pick a track above to run your first mock interview.
              </div>
            ) : (
              <div className="mt-4 overflow-hidden rounded-2xl border border-white/10">
                <table className="w-full text-left text-sm">
                  <thead className="bg-panel text-mute">
                    <tr>
                      {selectionMode && <th className="px-4 py-3 w-8"></th>}
                      <th className="px-4 py-3 font-medium">Track</th>
                      <th className="px-4 py-3 font-medium">Role</th>
                      <th className="px-4 py-3 font-medium">Score</th>
                      <th className="px-4 py-3 font-medium">Status</th>
                      <th className="px-4 py-3 font-medium">Date</th>
                      <th className="px-4 py-3 font-medium" colSpan={2}></th>
                    </tr>
                  </thead>
                  <tbody>
                    {sessions.map((s) => (
                      <tr
                        key={s.id}
                        className={`border-t border-white/5 transition ${selectionMode && selectedIds.has(s.id) ? "bg-coral/5" : ""}`}
                      >
                        {selectionMode && (
                          <td className="px-4 py-3">
                            <input
                              type="checkbox"
                              checked={selectedIds.has(s.id)}
                              onChange={() => toggleSelection(s.id)}
                              className="h-4 w-4 cursor-pointer accent-coral"
                            />
                          </td>
                        )}
                        <td className="px-4 py-3 capitalize">{s.track}</td>
                        <td className="px-4 py-3 text-mute">{s.role || "—"}</td>
                        <td className="px-4 py-3">
                          {s.overall_score != null ? `${s.overall_score}/10` : "—"}
                        </td>
                        <td className="px-4 py-3 text-mute capitalize">{s.status}</td>
                        <td className="px-4 py-3 text-mute">
                          {new Date(s.created_at).toLocaleDateString()}
                        </td>
                        <td className="px-4 py-3 text-right">
                          <Link to={`/results/${s.id}`} className="text-amber hover:underline">
                            View
                          </Link>
                        </td>
                        <td className="px-4 py-3 text-right">
                          <button
                            onClick={() => handleDelete(s.id)}
                            disabled={deletingId === s.id || deletingBulk}
                            className="text-sm text-mute transition hover:text-coral disabled:opacity-50"
                          >
                            {deletingId === s.id ? "Deleting..." : "Delete"}
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </section>
      </main>
      <Footer />
    </div>
  );
}
