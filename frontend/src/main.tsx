import { QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter } from "react-router";
import { RouterProvider } from "react-router/dom";
import { NotFound, RequireAuth } from "./App";
import { ME_KEY } from "./auth/session";
import { AppShell } from "./components/AppShell";
import { ApiError } from "./lib/api";
import { Claims } from "./pages/Claims";
import { Mapping } from "./pages/Mapping";
import { Overview } from "./pages/Overview";
import { Reconciliation } from "./pages/Reconciliation";
import { SignIn } from "./pages/SignIn";
import { SourceLayouts } from "./pages/SourceLayouts";
import { Uploads } from "./pages/Uploads";
import "./styles.css";

const queryClient: QueryClient = new QueryClient({
  queryCache: new QueryCache({
    // An expired session anywhere sends the user back to sign in.
    onError: (error) => {
      if (error instanceof ApiError && error.status === 401) queryClient.setQueryData(ME_KEY, null);
    },
  }),
  defaultOptions: {
    queries: { retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 2 },
  },
});

const router = createBrowserRouter([
  { path: "/login", element: <SignIn /> },
  {
    element: (
      <RequireAuth>
        <AppShell />
      </RequireAuth>
    ),
    children: [
      { index: true, element: <Overview /> },
      { path: "reconciliation", element: <Reconciliation /> },
      { path: "claims", element: <Claims /> },
      { path: "uploads", element: <Uploads /> },
      { path: "uploads/:uploadId/mapping", element: <Mapping /> },
      { path: "sources/:sourceId/layouts", element: <SourceLayouts /> },
      { path: "*", element: <NotFound /> },
    ],
  },
]);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);
