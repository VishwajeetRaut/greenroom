import { useEffect, useState } from "react";

export default function Losgann({ missingElements, onDismiss }) {
  const [visible, setVisible] = useState(false);
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    if (missingElements?.length > 0) {
      const timer = setTimeout(() => setVisible(true), 800);
      return () => clearTimeout(timer);
    }
  }, [missingElements]);

  const handleDismiss = () => {
    setDismissed(true);
    setTimeout(() => onDismiss?.(), 700);
  };

  if (!missingElements?.length || dismissed) return null;

  return (
    <>
      <style>{`
        @keyframes lotusLand {
          0% { transform: translateX(160%) translateY(20px); opacity: 0; }
          60% { transform: translateX(0%) translateY(6px); opacity: 1; }
          75% { transform: translateX(0%) translateY(-4px); }
          90% { transform: translateX(0%) translateY(2px); }
          100% { transform: translateX(0%) translateY(0px); opacity: 1; }
        }
        @keyframes lotusLeave {
          0% { transform: translateX(0%) translateY(0px) rotate(0deg); opacity: 1; }
          40% { transform: translateX(0%) translateY(-50px) rotate(-15deg); opacity: 1; }
          100% { transform: translateX(160%) translateY(-30px) rotate(25deg); opacity: 0; }
        }
        @keyframes frogBob {
          0%, 100% { transform: translateY(0px); }
          50% { transform: translateY(-6px); }
        }
        @keyframes thoughtPop {
          0% { transform: scale(0); opacity: 0; transform-origin: bottom right; }
          70% { transform: scale(1.05); opacity: 1; }
          100% { transform: scale(1); opacity: 1; }
        }
        @keyframes dotPop1 { 0%,60% { opacity:0; transform:scale(0); } 70% { opacity:1; transform:scale(1); } 100% { opacity:1; } }
        @keyframes dotPop2 { 0%,70% { opacity:0; transform:scale(0); } 80% { opacity:1; transform:scale(1); } 100% { opacity:1; } }
        @keyframes dotPop3 { 0%,80% { opacity:0; transform:scale(0); } 90% { opacity:1; transform:scale(1); } 100% { opacity:1; } }
        .losgann-enter { animation: lotusLand 1s cubic-bezier(0.34, 1.56, 0.64, 1) forwards; }
        .losgann-leave { animation: lotusLeave 0.7s ease-in forwards; }
        .frog-bob { animation: frogBob 2.5s ease-in-out infinite; }
        .thought-pop { animation: thoughtPop 0.5s ease-out 1s both; }
        .dot1 { animation: dotPop1 1.2s ease-out both; }
        .dot2 { animation: dotPop2 1.2s ease-out both; }
        .dot3 { animation: dotPop3 1.2s ease-out both; }
      `}</style>

     <div
        className={`fixed bottom-8 right-0 z-50 flex flex-col items-end ${
          visible ? (dismissed ? "losgann-leave" : "losgann-enter") : "opacity-0"
        }`}
      >
        {/* Thought bubble close to frog head */}
        <div className="flex flex-col items-end mb-1 mr-16">
          {/* Bubble */}
          <div className="thought-pop relative max-w-[200px] rounded-2xl border border-white/10 bg-panel p-4 shadow-xl">
            <button
              onClick={handleDismiss}
              className="absolute -right-2 -top-2 flex h-5 w-5 items-center justify-center rounded-full bg-panelLight text-xs text-mute hover:text-cream transition"
            >
              ✕
            </button>
            <p className="text-xs font-medium text-amber mb-2">Ribbit! You missed 🐸</p>
            <ul className="space-y-1.5">
              {missingElements.slice(0, 3).map((m, i) => (
                <li key={i} className="flex items-start gap-1.5 text-xs text-coral">
                  <span className="mt-0.5">•</span>
                  <span>{m}</span>
                </li>
              ))}
            </ul>
          </div>
          {/* Thought dots — small, close together, pointing down-right to frog head */}
          <div className="flex flex-col items-end gap-0.5 mt-1 mr-10">
            <div className="dot1 h-2.5 w-2.5 rounded-full bg-panel border border-white/10" />
            <div className="dot2 h-2 w-2 rounded-full bg-panel border border-white/10" />
            <div className="dot3 h-1.5 w-1.5 rounded-full bg-panel border border-white/10" />
          </div>
        </div>

        {/* Frog sitting on lily pad */}
        <div className="frog-bob">
          <svg width="150" height="145" viewBox="0 0 150 145" fill="none" xmlns="http://www.w3.org/2000/svg">

            {/* ── LILY PAD ── */}
            <ellipse cx="75" cy="128" rx="65" ry="18" fill="#15803d" />
            <ellipse cx="75" cy="125" rx="63" ry="16" fill="#16a34a" />
            <ellipse cx="75" cy="123" rx="61" ry="14" fill="#22c55e" opacity="0.5" />
            {/* Notch */}
            <path d="M69 110 Q75 104 81 110" fill="#1B2620" />
            {/* Veins */}
            <path d="M75 110 L75 138" stroke="#15803d" strokeWidth="1" opacity="0.5" />
            <path d="M75 120 L20 113" stroke="#15803d" strokeWidth="0.8" opacity="0.4" />
            <path d="M75 120 L130 113" stroke="#15803d" strokeWidth="0.8" opacity="0.4" />
            <path d="M75 120 L18 128" stroke="#15803d" strokeWidth="0.8" opacity="0.3" />
            <path d="M75 120 L132 128" stroke="#15803d" strokeWidth="0.8" opacity="0.3" />
            {/* Name on pad */}
            <text x="75" y="136" textAnchor="middle" fontSize="9" fill="#f6f1e7" fontFamily="serif" fontStyle="italic" opacity="0.9" letterSpacing="1">Losgann</text>
            {/* Shine */}
            <ellipse cx="50" cy="116" rx="12" ry="4" fill="white" opacity="0.06" transform="rotate(-15 50 116)" />

            {/* ── HIND LEGS — bent sitting, feet flat on pad ── */}
            {/* Left hind leg */}
            <path d="M54 112 Q42 116 36 122" stroke="#4ade80" strokeWidth="9" strokeLinecap="round" fill="none" />
            {/* Left foot flat */}
            <ellipse cx="32" cy="124" rx="9" ry="4" fill="#4ade80" transform="rotate(-10 32 124)" />
            {/* Left toes */}
            <path d="M24 122 L21 119M26 125 L22 125M30 127 L28 130" stroke="#3dbd72" strokeWidth="2" strokeLinecap="round" />

            {/* Right hind leg */}
            <path d="M96 112 Q108 116 114 122" stroke="#4ade80" strokeWidth="9" strokeLinecap="round" fill="none" />
            {/* Right foot flat */}
            <ellipse cx="118" cy="124" rx="9" ry="4" fill="#4ade80" transform="rotate(10 118 124)" />
            {/* Right toes */}
            <path d="M126 122 L129 119M124 125 L128 125M120 127 L122 130" stroke="#3dbd72" strokeWidth="2" strokeLinecap="round" />

            {/* ── BODY ── */}
            <ellipse cx="75" cy="105" rx="26" ry="20" fill="#4ade80" />
            {/* Belly */}
            <ellipse cx="75" cy="109" rx="16" ry="13" fill="#d9f99d" />
            {/* Spots */}
            <ellipse cx="57" cy="99" rx="3" ry="2" fill="#22c55e" opacity="0.5" />
            <ellipse cx="93" cy="99" rx="3" ry="2" fill="#22c55e" opacity="0.5" />

            {/* ── FRONT LEGS — resting down on pad, elbows bent ── */}
            {/* Left front */}
            <path d="M56 108 Q50 114 46 120" stroke="#4ade80" strokeWidth="7" strokeLinecap="round" fill="none" />
            {/* Left front hand flat on pad */}
            <ellipse cx="44" cy="122" rx="7" ry="3.5" fill="#4ade80" transform="rotate(-15 44 122)" />
            <path d="M38 121 L35 118M40 123 L37 124M44 124 L43 127" stroke="#3dbd72" strokeWidth="1.5" strokeLinecap="round" />

            {/* Right front */}
            <path d="M94 108 Q100 114 104 120" stroke="#4ade80" strokeWidth="7" strokeLinecap="round" fill="none" />
            {/* Right front hand flat on pad */}
            <ellipse cx="106" cy="122" rx="7" ry="3.5" fill="#4ade80" transform="rotate(15 106 122)" />
            <path d="M112 121 L115 118M110 123 L113 124M106 124 L107 127" stroke="#3dbd72" strokeWidth="1.5" strokeLinecap="round" />

            {/* ── HEAD ── */}
            <ellipse cx="75" cy="86" rx="25" ry="21" fill="#4ade80" />

            {/* Eye bumps */}
            <ellipse cx="60" cy="71" rx="11" ry="10" fill="#4ade80" />
            <ellipse cx="90" cy="71" rx="11" ry="10" fill="#4ade80" />
            {/* Eyes white */}
            <ellipse cx="60" cy="71" rx="9" ry="9" fill="white" />
            <ellipse cx="90" cy="71" rx="9" ry="9" fill="white" />
            {/* Iris */}
            <ellipse cx="60" cy="72" rx="6" ry="6" fill="#22c55e" />
            <ellipse cx="90" cy="72" rx="6" ry="6" fill="#22c55e" />
            {/* Pupils */}
            <ellipse cx="59" cy="72" rx="3.5" ry="3.5" fill="#1a0a00" />
            <ellipse cx="89" cy="72" rx="3.5" ry="3.5" fill="#1a0a00" />
            {/* Shine */}
            <ellipse cx="57" cy="70" rx="1.8" ry="1.8" fill="white" />
            <ellipse cx="87" cy="70" rx="1.8" ry="1.8" fill="white" />

            {/* Head stripe */}
            <path d="M66 67 Q75 64 84 67" stroke="#2d9e2d" strokeWidth="2" strokeLinecap="round" opacity="0.4" />

            {/* Nostrils */}
            <ellipse cx="71" cy="86" rx="2" ry="1.2" fill="#2d6a2d" />
            <ellipse cx="79" cy="86" rx="2" ry="1.2" fill="#2d6a2d" />

            {/* Big happy smile */}
            <path d="M62 94 Q75 104 88 94" stroke="#2d6a2d" strokeWidth="2.5" strokeLinecap="round" fill="none" />
            <path d="M66 96 Q75 102 84 96" fill="#ff9999" opacity="0.2" />

          </svg>
        </div>
      </div>
    </>
  );
}