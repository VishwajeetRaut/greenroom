import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { supabase } from "../lib/supabaseClient";

export default function AuthForm({ mode }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");
  const [forgotPassword, setForgotPassword] = useState(false);
  const navigate = useNavigate();

  const isSignup = mode === "signup";

  const handleForgotPassword = async (e) => {
    e.preventDefault();
    setError("");
    setMessage("");
    setLoading(true);
    try {
      const { error: resetError } = await supabase.auth.resetPasswordForEmail(email, {
        redirectTo: `${window.location.origin}/login`,
      });
      if (resetError) throw resetError;
      setMessage("Password reset link sent — check your email.");
    } catch (err) {
      setError(err.message || "Something went wrong. Try again.");
    } finally {
      setLoading(false);
    }
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError("");
    setMessage("");
    setLoading(true);

    try {
      if (isSignup) {
        const { error: signUpError } = await supabase.auth.signUp({ email, password });
        if (signUpError) throw signUpError;
        setMessage("Check your email to confirm your account, then sign in.");
      } else {
        const { error: signInError } = await supabase.auth.signInWithPassword({
          email,
          password
        });
        if (signInError) throw signInError;
        navigate("/dashboard");
      }
    } catch (err) {
      setError(err.message || "Something went wrong. Try again.");
    } finally {
      setLoading(false);
    }
  };

  if (forgotPassword) {
    return (
      <div className="mx-auto flex min-h-[70vh] max-w-md flex-col justify-center px-6 py-16">
        <h1 className="font-display text-3xl tracking-tight">Reset your password</h1>
        <p className="mt-2 text-sm text-mute">
          Enter your email and we'll send you a link to set a new password.
        </p>

        <form onSubmit={handleForgotPassword} className="mt-8 space-y-4">
          <div>
            <label htmlFor="email" className="block text-sm text-mute">
              Email
            </label>
            <input
              id="email"
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="mt-1 w-full rounded-lg border border-white/10 bg-panel px-4 py-2.5 text-cream outline-none focus:border-amber/50"
              placeholder="you@example.com"
            />
          </div>

          {error && <p className="text-sm text-coral">{error}</p>}
          {message && <p className="text-sm text-sage">{message}</p>}

          <button
            type="submit"
            disabled={loading}
            className="w-full rounded-full bg-amber px-6 py-3 text-sm font-medium text-ink transition hover:bg-amberDark disabled:opacity-60"
          >
            {loading ? "Sending..." : "Send reset link"}
          </button>
        </form>

        <p className="mt-6 text-center text-sm text-mute">
          <button
            onClick={() => { setForgotPassword(false); setError(""); setMessage(""); }}
            className="text-amber hover:underline"
          >
            Back to sign in
          </button>
        </p>
      </div>
    );
  }

  return (
    <div className="mx-auto flex min-h-[70vh] max-w-md flex-col justify-center px-6 py-16">
      <h1 className="font-display text-3xl tracking-tight">
        {isSignup ? "Set up your greenroom" : "Welcome back"}
      </h1>
      <p className="mt-2 text-sm text-mute">
        {isSignup
          ? "Create a free account to save your sessions and track progress over time."
          : "Sign in to pick up where you left off."}
      </p>

      <form onSubmit={handleSubmit} className="mt-8 space-y-4">
        <div>
          <label htmlFor="email" className="block text-sm text-mute">
            Email
          </label>
          <input
            id="email"
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="mt-1 w-full rounded-lg border border-white/10 bg-panel px-4 py-2.5 text-cream outline-none focus:border-amber/50"
            placeholder="you@example.com"
          />
        </div>
        <div>
          <div className="flex items-center justify-between">
            <label htmlFor="password" className="block text-sm text-mute">
              Password
            </label>
            {!isSignup && (
              <button
                type="button"
                onClick={() => { setForgotPassword(true); setError(""); setMessage(""); }}
                className="text-xs text-amber hover:underline"
              >
                Forgot password?
              </button>
            )}
          </div>
          <input
            id="password"
            type="password"
            required
            minLength={6}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="mt-1 w-full rounded-lg border border-white/10 bg-panel px-4 py-2.5 text-cream outline-none focus:border-amber/50"
            placeholder="At least 6 characters"
          />
        </div>

        {error && <p className="text-sm text-coral">{error}</p>}
        {message && <p className="text-sm text-sage">{message}</p>}

        <button
          type="submit"
          disabled={loading}
          className="w-full rounded-full bg-amber px-6 py-3 text-sm font-medium text-ink transition hover:bg-amberDark disabled:opacity-60"
        >
          {loading ? "One moment..." : isSignup ? "Create account" : "Sign in"}
        </button>
      </form>

      <p className="mt-6 text-center text-sm text-mute">
        {isSignup ? (
          <>
            Already have an account?{" "}
            <a href="/login" className="text-amber hover:underline">
              Sign in
            </a>
          </>
        ) : (
          <>
            New here?{" "}
            <a href="/signup" className="text-amber hover:underline">
              Create an account
            </a>
          </>
        )}
      </p>
    </div>
  );
}
