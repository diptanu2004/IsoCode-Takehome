import { useState, type FormEvent } from "react";
import { Navigate, useLocation } from "react-router";
import { useMe, useSignIn } from "../auth/session";
import { Logo } from "../components/Logo";
import { Alert, Button, TextField, useTitle } from "../components/ui";

export function SignIn() {
  useTitle("Sign in");
  const { data: me } = useMe();
  const signIn = useSignIn();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [missing, setMissing] = useState(false);

  const from = (location.state as { from?: string } | null)?.from ?? "/";
  if (me) return <Navigate to={from} replace />;

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    // Read the form itself: browser autofill doesn't always fire React's change events.
    const form = new FormData(event.currentTarget);
    const values = { email: String(form.get("email") ?? "").trim(), password: String(form.get("password") ?? "") };
    if (!values.email || !values.password) {
      setMissing(true);
      return;
    }
    setMissing(false);
    signIn.mutate(values);
  }

  return (
    <div className="flex min-h-dvh flex-col items-center justify-center px-4 py-12">
      <div className="w-full max-w-sm">
        <Logo className="mb-8 flex justify-center" />
        <div className="rounded-xl border border-line bg-surface p-6 shadow-xs sm:p-8">
          <h1 className="text-lg font-semibold tracking-tight">Sign in</h1>
          <p className="mt-1 text-sm text-muted">Partner payment reconciliation for Company A.</p>

          <form onSubmit={submit} className="mt-6 space-y-4" noValidate>
            {missing ? (
              <Alert>Enter your email and password.</Alert>
            ) : (
              signIn.error && <Alert>{signIn.error.message}</Alert>
            )}
            <TextField
              label="Email"
              name="email"
              type="email"
              autoComplete="username"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
            <TextField
              label="Password"
              name="password"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            <Button type="submit" className="w-full" loading={signIn.isPending}>
              Sign in
            </Button>
          </form>
        </div>
        <p className="mt-6 text-center text-xs text-muted">Demo accounts are listed in deployment.md.</p>
      </div>
    </div>
  );
}
