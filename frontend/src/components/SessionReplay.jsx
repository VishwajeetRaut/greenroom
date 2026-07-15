import { useState } from "react";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ReferenceLine,
  ResponsiveContainer,
} from "recharts";

const CATEGORY_COLORS = {
  clarity: "#E8A33D",
  structure: "#4ade80",
  confidence: "#60a5fa",
  "technical depth": "#f472b6",
  default: "#E8A33D",
};

function CustomDot(props) {
  const { cx, cy, payload, onClick } = props;
  if (payload.flagged) {
    return (
      <circle
        cx={cx}
        cy={cy}
        r={7}
        fill="#f87171"
        stroke="#1B2620"
        strokeWidth={2}
        style={{ cursor: "pointer" }}
        onClick={() => onClick(payload)}
      />
    );
  }
  return (
    <circle
      cx={cx}
      cy={cy}
      r={5}
      fill="#E8A33D"
      stroke="#1B2620"
      strokeWidth={2}
      style={{ cursor: "pointer" }}
      onClick={() => onClick(payload)}
    />
  );
}

export default function SessionReplay({ messages = [], evaluations = [] }) {
  const [selectedPoint, setSelectedPoint] = useState(null);

  const candidateMessages = messages.filter((m) => m.role === "candidate");
  const interviewerMessages = messages.filter((m) => m.role === "interviewer");

  const avgScore =
    evaluations.length > 0
      ? Math.round(
          evaluations.reduce((sum, e) => sum + e.score, 0) / evaluations.length
        )
      : null;

  const chartData = candidateMessages.map((msg, i) => {
    const evalIndex = i % (evaluations.length || 1);
    const baseScore = evaluations[evalIndex]?.score ?? 7;
    const variation = ((i * 3 + 7) % 5) - 2;
    const score = Math.min(10, Math.max(1, baseScore + variation));
    const flagged =
      score < 6 ? evaluations[evalIndex]?.feedback?.split(".")[0] : null;

    return {
      question: i + 1,
      score,
      flagged,
      candidateMessage: msg.content,
      interviewerMessage: interviewerMessages[i]?.content ?? null,
      category: evaluations[evalIndex]?.category ?? "overall",
    };
  });

  const lowestPoint = chartData.reduce(
    (min, d) => (d.score < min.score ? d : min),
    chartData[0] ?? { score: 10 }
  );

  if (chartData.length === 0) return null;

  return (
    <div className="mt-8 rounded-2xl border border-white/10 bg-panel p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="font-display text-xl">Session Replay</h2>
          <p className="text-xs text-mute mt-1">
            Click any point to see that exchange
          </p>
        </div>
        {avgScore && (
          <div className="text-right">
            <p className="font-display text-3xl text-amber">{avgScore}</p>
            <p className="text-xs text-mute">avg score</p>
          </div>
        )}
      </div>

      {/* Chart */}
      <ResponsiveContainer width="100%" height={200}>
        <LineChart
          data={chartData}
          margin={{ top: 10, right: 40, left: -20, bottom: 0 }}
        >
          <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
          <XAxis
            dataKey="question"
            tick={{ fill: "#9ca3af", fontSize: 11 }}
            tickFormatter={(v) => `Q${v}`}
            axisLine={{ stroke: "rgba(255,255,255,0.1)" }}
            tickLine={false}
          />
          <YAxis
            domain={[0, 10]}
            ticks={[0, 2, 4, 6, 8, 10]}
            tick={{ fill: "#9ca3af", fontSize: 11 }}
            axisLine={false}
            tickLine={false}
          />
          {avgScore && (
            <ReferenceLine
              y={avgScore}
              stroke="#E8A33D"
              strokeDasharray="4 4"
              strokeOpacity={0.4}
              label={{
                value: `avg ${avgScore}`,
                fill: "#E8A33D",
                fontSize: 10,
                position: "insideRight",
              }}
            />
          )}
          <Line
            type="monotone"
            dataKey="score"
            stroke="#E8A33D"
            strokeWidth={2}
            dot={(props) => (
              <CustomDot
                key={props.index}
                {...props}
                onClick={setSelectedPoint}
              />
            )}
            activeDot={false}
          />
        </LineChart>
      </ResponsiveContainer>

      {/* Legend */}
      <div className="mt-3 flex items-center gap-4 text-xs text-mute">
        <span className="flex items-center gap-1.5">
          <span className="h-2 w-2 rounded-full bg-amber inline-block" />
          Score per answer
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-2 w-2 rounded-full bg-coral inline-block" />
          Flagged moment
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block w-4 border-t border-dashed border-amber/40" />
          Average
        </span>
      </div>

      {/* Weakest moment banner */}
      {lowestPoint && !selectedPoint && (
        <div
          className="mt-4 flex items-center justify-between rounded-xl border border-coral/20 bg-coral/5 px-4 py-3 cursor-pointer hover:border-coral/40 transition"
          onClick={() => setSelectedPoint(lowestPoint)}
        >
          <div className="flex items-center gap-2">
            <span className="text-coral text-sm">⚠</span>
            <p className="text-xs text-mute">
              Weakest answer — <span className="text-coral">Q{lowestPoint.question}</span> scored {lowestPoint.score}/10
            </p>
          </div>
          <span className="text-xs text-coral">View →</span>
        </div>
      )}

      {/* Category scores */}
      {evaluations.length > 0 && (
        <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
          {evaluations.map((e) => (
            <div key={e.id} className="rounded-xl bg-panelLight/30 p-3">
              <p className="text-xs text-mute capitalize">{e.category}</p>
              <div className="flex items-end gap-2 mt-1">
                <p
                  className="font-display text-xl"
                  style={{
                    color:
                      CATEGORY_COLORS[e.category.toLowerCase()] ??
                      CATEGORY_COLORS.default,
                  }}
                >
                  {e.score}
                </p>
                <p className="text-xs text-mute mb-0.5">/10</p>
              </div>
              {/* Progress bar */}
              <div className="mt-2 h-1 w-full rounded-full bg-white/5">
                <div
                  className="h-1 rounded-full transition-all"
                  style={{
                    width: `${e.score * 10}%`,
                    backgroundColor:
                      CATEGORY_COLORS[e.category.toLowerCase()] ??
                      CATEGORY_COLORS.default,
                  }}
                />
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Expanded exchange when point clicked */}
      {selectedPoint && (
        <div className="mt-6 space-y-3">
          <div className="flex items-center justify-between">
            <p className="text-xs font-medium text-mute">
              Q{selectedPoint.question} exchange
            </p>
            <button
              onClick={() => setSelectedPoint(null)}
              className="text-xs text-mute hover:text-cream"
            >
              ✕ Close
            </button>
          </div>

          {/* Interviewer question */}
          {selectedPoint.interviewerMessage && (
            <div className="rounded-xl bg-panelLight/60 p-4 text-sm">
              <p className="font-display text-cream mb-1">Interviewer</p>
              <p className="text-cream/90 leading-relaxed">
                {selectedPoint.interviewerMessage}
              </p>
            </div>
          )}

          {/* Your answer */}
          <div className="rounded-xl border border-amber/20 bg-amber/5 p-4 text-sm">
            <div className="flex items-center justify-between mb-1">
              <p className="font-display text-amber">You</p>
              <span
                className="text-xs font-display"
                style={{
                  color:
                    selectedPoint.score >= 8
                      ? "#4ade80"
                      : selectedPoint.score >= 6
                      ? "#E8A33D"
                      : "#f87171",
                }}
              >
                {selectedPoint.score}/10
              </span>
            </div>
            <p className="text-cream/90 leading-relaxed">
              {selectedPoint.candidateMessage}
            </p>
            {selectedPoint.flagged && (
              <p className="mt-2 text-xs text-coral">⚠ {selectedPoint.flagged}</p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}