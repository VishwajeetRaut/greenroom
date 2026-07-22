import { useCallback, useRef, useState } from "react";
import { api } from "../lib/api";

export function useSpeechSynthesis() {
  const [isSpeaking, setIsSpeaking] = useState(false);
  const audioRef = useRef(null);
  const audioUrlRef = useRef(null);
  const cancelledRef = useRef(false);

  const revokeAudioUrl = () => {
    if (audioUrlRef.current) {
      URL.revokeObjectURL(audioUrlRef.current);
      audioUrlRef.current = null;
    }
  };

  const speak = useCallback(async (text) => {
    if (!text) return;
    cancelledRef.current = false;
    setIsSpeaking(true);

    try {
      const url = await api.speak(text);
      // stop() was called while the fetch was in flight — discard the audio
      if (cancelledRef.current) {
        URL.revokeObjectURL(url);
        return;
      }
      audioUrlRef.current = url;
      const audio = new Audio(url);
      audioRef.current = audio;
      audio.onended = () => { setIsSpeaking(false); revokeAudioUrl(); };
      audio.onerror = () => { revokeAudioUrl(); fallbackToBrowser(text); };
      await audio.play();
    } catch {
      if (!cancelledRef.current) fallbackToBrowser(text);
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
    cancelledRef.current = true;
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.currentTime = 0;
      audioRef.current = null;
    }
    revokeAudioUrl();
    if ("speechSynthesis" in window) {
      window.speechSynthesis.cancel();
    }
    setIsSpeaking(false);
  }, []);

  // pause mid-sentence (keeps position so resume() can continue from same spot)
  const pause = useCallback(() => {
    cancelledRef.current = true;
    if (audioRef.current && !audioRef.current.paused) {
      audioRef.current.pause();
    }
    if ("speechSynthesis" in window) {
      window.speechSynthesis.pause();
    }
    setIsSpeaking(false);
  }, []);

  // resume from where pause() stopped
  const resume = useCallback(() => {
    cancelledRef.current = false;
    if (audioRef.current && audioRef.current.paused && !audioRef.current.ended) {
      audioRef.current.play();
      setIsSpeaking(true);
    } else if ("speechSynthesis" in window && window.speechSynthesis.paused) {
      window.speechSynthesis.resume();
      setIsSpeaking(true);
    }
  }, []);

  return { isSpeaking, speak, stop, pause, resume };
}
