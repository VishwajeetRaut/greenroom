import { useCallback, useRef, useState } from "react";
import { api } from "../lib/api";

export function useSpeechSynthesis() {
  const [isSpeaking, setIsSpeaking] = useState(false);
  const audioRef = useRef(null);
  const audioUrlRef = useRef(null);

  const revokeAudioUrl = () => {
    if (audioUrlRef.current) {
      URL.revokeObjectURL(audioUrlRef.current);
      audioUrlRef.current = null;
    }
  };

  const speak = useCallback(async (text) => {
    if (!text) return;
    setIsSpeaking(true);

    try {
      const url = await api.speak(text);
      audioUrlRef.current = url;
      const audio = new Audio(url);
      audioRef.current = audio;
      audio.onended = () => { setIsSpeaking(false); revokeAudioUrl(); };
      audio.onerror = () => { revokeAudioUrl(); fallbackToBrowser(text); };
      await audio.play();
    } catch {
      fallbackToBrowser(text);
    }

    function fallbackToBrowser(value) {
      if (!("speechSynthesis" in window)) {
        setIsSpeaking(false);
        return;
      }
      const utterance = new SpeechSynthesisUtterance(value);
      utterance.rate = 1;
      utterance.onend = () => setIsSpeaking(false);
      window.speechSynthesis.speak(utterance);
    }
  }, []);

  const stop = useCallback(() => {
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.currentTime = 0;
    }
    revokeAudioUrl();
    if ("speechSynthesis" in window) {
      window.speechSynthesis.cancel();
    }
    setIsSpeaking(false);
  }, []);

  return { isSpeaking, speak, stop };
}
