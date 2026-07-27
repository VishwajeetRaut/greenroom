import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import Navbar from "../components/Navbar";
import { api } from "../lib/api";

const TRACK_LABELS = {
  behavioral: "Behavioral",
  technical: "Technical",
  "system-design": "System Design",
};

const TRACK_COLORS = {
  behavioral: "bg-amber",
  technical: "bg-sage",
  "system-design": "bg-coral",
};

const LANG_COLORS = ["bg-amber", "bg-sage", "bg-coral", "bg-blue-400"];

function KpiCard({ label, value, sub }) {
  return (
    <div className="rounded-2xl border border-white/10 bg-panel px-6 py-5">
      <p className="text-xs uppercase tracking-wide text-mute">{label}</p>
      <p className="mt-1 font-display text-3xl text-cream">{value ?? "—"}</p>
      {sub && <p className="mt-1 text-xs text-mute">{sub}</p>}
    </div>
  );
}

function HBar({ label, value, max, color = "bg-amber", suffix = "" }) {
  const pct = max > 0 ? Math.round((value / max) * 100) : 0;
  return (
    <div className="flex items-center gap-3">
      <span className="w-28 shrink-0 text-right text-xs text-mute truncate">{label}</span>
      <div className="flex-1 h-2 rounded-full bg-white/5 overflow-hidden">
        <div className={`h-full rounded-full ${color} transition-all duration-700`} style={{ width: `${pct}%` }} />
      </div>
      <span className="w-10 text-right text-xs text-cream/70 tabular-nums">{value}{suffix}</span>
    </div>
  );
}

function DailyChart({ data }) {
  const entries = Object.entries(data);
  const max = Math.max(...entries.map(([, v]) => v), 1);

  return (
    <div className="flex items-end gap-1 h-20">
      {entries.map(([day, count]) => {
        const pct = Math.round((count / max) * 100);
        const label = new Date(day + "T00:00:00").toLocaleDateString("en-IE", { month: "short", day: "numeric" });
        return (
          <div key={day} className="flex flex-col items-center flex-1 gap-1 group relative">
            <div
              className="w-full rounded-t bg-amber/70 group-hover:bg-amber transition-all duration-300"
              style={{ height: `${Math.max(pct, 2)}%` }}
              title={`${label}: ${count} session${count !== 1 ? "s" : ""}`}
            />
            {/* show label every ~4 bars to avoid crowding */}
            {entries.indexOf(entries.find(([d]) => d === day)) % 4 === 0 && (
              <span className="text-[9px] text-mute rotate-45 origin-left mt-1">{label}</span>
            )}
          </div>
        );
      })}
    </div>
  );
}

function ScoreDistChart({ data }) {
  const max = Math.max(...Object.values(data), 1);
  const colors = ["bg-red-400", "bg-orange-400", "bg-amber", "bg-lime-400", "bg-emerald-400"];
  return (
    <div className="flex items-end gap-2 h-20">
      {Object.entries(data).map(([bucket, count], i) => {
        const pct = Math.round((count / max) * 100);
        return (
          <div key={bucket} className="flex flex-col items-center flex-1 gap-1 group">
            <span className="text-xs text-cream/70 tabular-nums">{count}</span>
            <div
              className={`w-full rounded-t ${colors[i]} transition-all duration-500`}
              style={{ height: `${Math.max(pct, 4)}%` }}
              title={`Score ${bucket}: ${count}`}
            />
            <span className="text-[10px] text-mute">{bucket}</span>
          </div>
        );
      })}
    </div>
  );
}

const DAY_OPTIONS = [7, 14, 30, 90];
const TRACK_OPTIONS = [
  { value: "", label: "All tracks" },
  { value: "behavioral", label: "Behavioral" },
  { value: "technical", label: "Technical" },
  { value: "system-design", label: "System Design" },
];

export default function Telemetry() {
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [days, setDays] = useState(14);
  const [track, setTrack] = useState("");
  const navigate = useNavigate();

  useEffect(() => {
    setLoading(true);
    api.getStats({ days, track: track || undefined })
      .then(setStats)
      .catch((err) => {
        if (err.message?.includes("401") || err.message?.includes("403")) {
          navigate("/login", { replace: true });
        } else {
          setError(err.message || "Failed to load telemetry data.");
        }
      })
      .finally(() => setLoading(false));
  }, [navigate, days, track]);

  const trackKeys = ["behavioral", "technical", "system-design"];
  const maxTrackSessions = stats ? Math.max(...trackKeys.map(t => stats.sessions_by_track[t] || 0), 1) : 1;
  const langEntries = stats
    ? Object.entries(stats.language_usage).sort(([, a], [, b]) => b - a)
    : [];
  const maxLang = langEntries.length ? langEntries[0][1] : 1;
  const completionRate = stats && stats.total_sessions > 0
    ? Math.round((stats.completed_sessions / stats.total_sessions) * 100)
    : 0;

  return (
    <div className="min-h-screen bg-stage">
      <Navbar />
      <main className="mx-auto max-w-6xl px-6 py-10">
        <div className="mb-8 flex flex-wrap items-center justify-between gap-4">
          <h1 className="font-display text-2xl text-cream">Telemetry</h1>
          <div className="flex items-center gap-3">
            <select
              value={track}
              onChange={(e) => setTrack(e.target.value)}
              className="rounded-lg border border-white/10 bg-panel px-3 py-1.5 text-sm text-cream outline-none focus:border-amber/40"
            >
              {TRACK_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </select>
            <select
              value={days}
              onChange={(e) => setDays(Number(e.target.value))}
              className="rounded-lg border border-white/10 bg-panel px-3 py-1.5 text-sm text-cream outline-none focus:border-amber/40"
            >
              {DAY_OPTIONS.map((d) => (
                <option key={d} value={d}>Last {d} days</option>
              ))}
            </select>
          </div>
        </div>

        {loading && (
          <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
            {[0,1,2,3].map(i => (
              <div key={i} className="h-24 animate-pulse rounded-2xl bg-panel" />
            ))}
          </div>
        )}

        {error && (
          <p className="rounded-xl border border-coral/30 bg-coral/5 px-5 py-4 text-sm text-coral">{error}</p>
        )}

        {stats && (
          <div className="space-y-6">
            {/* KPI row */}
            <div className="grid grid-cols-3 gap-4">
              <KpiCard label="Total sessions" value={stats.total_sessions} />
              <KpiCard label="Completed" value={stats.completed_sessions} sub={`${completionRate}% completion rate`} />
              <KpiCard label="Avg score" value={stats.avg_score > 0 ? `${stats.avg_score} / 10` : "—"} sub="across completed sessions" />
            </div>

            {/* Charts grid */}
            <div className="grid grid-cols-1 gap-6 md:grid-cols-2">

              {/* Sessions by track */}
              <div className="rounded-2xl border border-white/10 bg-panel px-6 py-5">
                <h2 className="mb-4 text-sm font-medium text-cream">Sessions by track</h2>
                <div className="space-y-3">
                  {trackKeys.map((t) => (
                    <HBar
                      key={t}
                      label={TRACK_LABELS[t]}
                      value={stats.sessions_by_track[t] || 0}
                      max={maxTrackSessions}
                      color={TRACK_COLORS[t]}
                    />
                  ))}
                </div>
              </div>

              {/* Avg score by track */}
              <div className="rounded-2xl border border-white/10 bg-panel px-6 py-5">
                <h2 className="mb-4 text-sm font-medium text-cream">Avg score by track</h2>
                <div className="space-y-3">
                  {trackKeys.map((t) => (
                    <HBar
                      key={t}
                      label={TRACK_LABELS[t]}
                      value={stats.avg_score_by_track[t] || 0}
                      max={10}
                      color={TRACK_COLORS[t]}
                      suffix=" / 10"
                    />
                  ))}
                </div>
                <div className="mt-4 space-y-1">
                  {trackKeys.map((t) => (
                    <div key={t} className="flex items-center justify-between text-xs text-mute">
                      <span>{TRACK_LABELS[t]}</span>
                      <span>{stats.completion_rate_by_track[t] || 0}% completion</span>
                    </div>
                  ))}
                </div>
              </div>

              {/* Sessions over the selected window */}
              <div className="rounded-2xl border border-white/10 bg-panel px-6 py-5">
                <h2 className="mb-4 text-sm font-medium text-cream">Sessions — last {days} days</h2>
                <DailyChart data={stats.sessions_by_day} />
              </div>

              {/* Score distribution */}
              <div className="rounded-2xl border border-white/10 bg-panel px-6 py-5">
                <h2 className="mb-4 text-sm font-medium text-cream">Score distribution</h2>
                {Object.values(stats.score_distribution).every(v => v === 0) ? (
                  <p className="text-xs text-mute">No completed sessions with scores yet.</p>
                ) : (
                  <ScoreDistChart data={stats.score_distribution} />
                )}
              </div>

              {/* Coding language usage */}
              {langEntries.length > 0 && (
                <div className="rounded-2xl border border-white/10 bg-panel px-6 py-5 md:col-span-2">
                  <h2 className="mb-4 text-sm font-medium text-cream">Coding language usage</h2>
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                    {langEntries.map(([lang, count], i) => (
                      <HBar
                        key={lang}
                        label={lang.charAt(0).toUpperCase() + lang.slice(1)}
                        value={count}
                        max={maxLang}
                        color={LANG_COLORS[i % LANG_COLORS.length]}
                        suffix=" runs"
                      />
                    ))}
                  </div>
                </div>
              )}

            </div>
          </div>
        )}
      </main>
    </div>
  );
}
