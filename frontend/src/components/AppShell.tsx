import {
  FileUp,
  HandCoins,
  LayoutDashboard,
  ListChecks,
  LogOut,
  Menu,
  ReceiptIndianRupee,
  Settings as SettingsIcon,
  X,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router";
import { ROLE_LABELS, can, useMe, useSignOut, type Me, type Permission } from "../auth/session";
import { Logo } from "./Logo";
import { cx } from "./ui";

interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  permission: Permission;
}

// Screens are added here as they are built, so the menu never leads to a dead end.
const NAV: NavItem[] = [
  { to: "/", label: "Overview", icon: LayoutDashboard, permission: "dashboard.view" },
  { to: "/reconciliation", label: "Reconciliation", icon: ListChecks, permission: "recon.view" },
  { to: "/claims", label: "Claims", icon: HandCoins, permission: "recon.view" },
  { to: "/uploads", label: "Uploads", icon: FileUp, permission: "recon.view" },
  { to: "/rate-cards", label: "Rate cards", icon: ReceiptIndianRupee, permission: "rate_card.view" },
  { to: "/settings", label: "Settings", icon: SettingsIcon, permission: "recon.view" },
];

function Navigation({ me }: { me: Me }) {
  return (
    <nav aria-label="Main" className="flex flex-col gap-0.5 px-3">
      {NAV.filter((item) => can(me, item.permission)).map(({ to, label, icon: Icon }) => (
        <NavLink
          key={to}
          to={to}
          end={to === "/"}
          className={({ isActive }) =>
            cx(
              "flex h-9 items-center gap-2.5 rounded-md px-2.5 text-sm font-medium transition-colors",
              isActive ? "bg-accent-soft text-accent" : "text-muted hover:bg-canvas hover:text-ink",
            )
          }
        >
          <Icon className="size-4" aria-hidden />
          {label}
        </NavLink>
      ))}
    </nav>
  );
}

function initials(name: string): string {
  return name
    .split(/\s+/)
    .map((part) => part[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();
}

function UserPanel({ me }: { me: Me }) {
  const signOut = useSignOut();
  return (
    <div className="flex items-center gap-3 border-t border-line px-4 py-3">
      <div className="grid size-8 shrink-0 place-items-center rounded-full bg-canvas text-xs font-semibold text-muted">
        {initials(me.name)}
      </div>
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium text-ink">{me.name}</p>
        <p className="truncate text-xs text-muted">{ROLE_LABELS[me.role]}</p>
      </div>
      <button
        type="button"
        onClick={() => signOut.mutate()}
        disabled={signOut.isPending}
        className="grid size-8 place-items-center rounded-md text-muted hover:bg-canvas hover:text-ink"
        aria-label="Sign out"
        title="Sign out"
      >
        <LogOut className="size-4" aria-hidden />
      </button>
    </div>
  );
}

function Sidebar({ me }: { me: Me }) {
  return (
    <div className="flex h-full flex-col bg-surface">
      <div className="flex h-14 items-center px-5">
        <Logo />
      </div>
      <div className="flex-1 overflow-y-auto py-2">
        <Navigation me={me} />
      </div>
      <UserPanel me={me} />
    </div>
  );
}

export function AppShell() {
  const { data: me } = useMe();
  const [menuOpen, setMenuOpen] = useState(false);
  const location = useLocation();

  useEffect(() => setMenuOpen(false), [location.pathname]);
  useEffect(() => {
    if (!menuOpen) return;
    const close = (event: KeyboardEvent) => event.key === "Escape" && setMenuOpen(false);
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [menuOpen]);

  if (!me) return null;

  return (
    <div className="min-h-dvh">
      <aside className="fixed inset-y-0 left-0 hidden w-60 border-r border-line lg:block">
        <Sidebar me={me} />
      </aside>

      <header className="sticky top-0 z-20 flex h-14 items-center justify-between border-b border-line bg-surface px-4 lg:hidden">
        <Logo />
        <button
          type="button"
          onClick={() => setMenuOpen(true)}
          className="grid size-9 place-items-center rounded-md text-muted hover:bg-canvas hover:text-ink"
          aria-label="Open menu"
          aria-expanded={menuOpen}
        >
          <Menu className="size-5" aria-hidden />
        </button>
      </header>

      {menuOpen && (
        <div className="fixed inset-0 z-30 lg:hidden" role="dialog" aria-modal="true" aria-label="Menu">
          <div className="absolute inset-0 bg-ink/30" onClick={() => setMenuOpen(false)} />
          <div className="absolute inset-y-0 left-0 w-72 max-w-[85vw] shadow-xl">
            <Sidebar me={me} />
            <button
              type="button"
              onClick={() => setMenuOpen(false)}
              className="absolute top-3 right-3 grid size-8 place-items-center rounded-md text-muted hover:bg-canvas"
              aria-label="Close menu"
            >
              <X className="size-4" aria-hidden />
            </button>
          </div>
        </div>
      )}

      <main className="lg:pl-60">
        <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8 lg:py-8">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
