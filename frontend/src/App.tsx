import { CloudOff, Compass } from "lucide-react";
import type { ReactNode } from "react";
import { Link, Navigate, useLocation } from "react-router";
import { useMe } from "./auth/session";
import { Button, EmptyState, Spinner, useTitle } from "./components/ui";

export function RequireAuth({ children }: { children: ReactNode }) {
  const { data: me, isPending, isError, refetch, isRefetching } = useMe();
  const location = useLocation();

  if (isPending) {
    return (
      <div className="grid min-h-dvh place-items-center text-muted" aria-label="Loading">
        <Spinner />
      </div>
    );
  }
  if (isError) {
    return (
      <div className="grid min-h-dvh place-items-center px-4">
        <EmptyState
          icon={CloudOff}
          title="Can't reach Settle"
          action={
            <Button variant="secondary" loading={isRefetching} onClick={() => refetch()}>
              Try again
            </Button>
          }
        >
          The server didn't respond. Check your connection and try again.
        </EmptyState>
      </div>
    );
  }
  if (!me) return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  return children;
}

export function NotFound() {
  useTitle("Not found");
  return (
    <EmptyState
      icon={Compass}
      title="Page not found"
      action={
        <Link to="/" className="text-sm font-medium text-accent hover:text-accent-strong">
          Go to the overview
        </Link>
      }
    >
      This page doesn't exist or has moved.
    </EmptyState>
  );
}
