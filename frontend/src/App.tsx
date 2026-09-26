import { useEffect, useRef, useState } from "react";
import { Link, Outlet, useLocation } from "react-router-dom";
import { getConfig } from "./api";
import { HeaderActionContext } from "./HeaderActionContext";
import "./App.css";

function ScrollToTop() {
  const { pathname } = useLocation();
  useEffect(() => {
    window.scrollTo(0, 0);
  }, [pathname]);
  return null;
}

const LINKS = [
  { to: "/", label: "全局配置", short: "全" },
  { to: "/apis", label: "接口列表", short: "接" },
  { to: "/schedules", label: "计划任务", short: "计" },
  { to: "/groups", label: "用户/群组", short: "组" },
  { to: "/auth", label: "认证", short: "证" },
];

const POLL_INTERVAL_MS = 10_000;

function ConnectionBanner() {
  const [disconnected, setDisconnected] = useState(false);
  const [reconnected, setReconnected] = useState(false);
  const wasDisconnected = useRef(false);

  useEffect(() => {
    const id = setInterval(async () => {
      try {
        await getConfig();
        if (wasDisconnected.current) {
          setReconnected(true);
          wasDisconnected.current = false;
          setTimeout(() => setReconnected(false), 3000);
        }
        setDisconnected(false);
      } catch {
        setDisconnected(true);
        wasDisconnected.current = true;
      }
    }, POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, []);

  if (disconnected) {
    return (
      <div className="connection-banner connection-banner--disconnected" role="status">
        连接已断开，正在重连…
      </div>
    );
  }
  if (reconnected) {
    return (
      <div className="connection-banner connection-banner--reconnected" role="status">
        已重新连接
      </div>
    );
  }
  return null;
}

export default function App() {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(true);
  const [headerAction, setHeaderAction] = useState<React.ReactNode>(null);
  const location = useLocation();

  const toggleSidebar = () => setSidebarCollapsed((c) => !c);
  const closeSidebar = () => setSidebarCollapsed(true);

  return (
    <HeaderActionContext.Provider value={{ setAction: setHeaderAction }}>
    <div className={`app ${sidebarCollapsed ? "sidebar-collapsed" : ""}`}>
      <ScrollToTop />
      <ConnectionBanner />
      <header className="app-header">
        <button
          type="button"
          className="app-header__menu"
          onClick={toggleSidebar}
          aria-label={sidebarCollapsed ? "打开菜单" : "折叠菜单"}
          title={sidebarCollapsed ? "打开菜单" : "折叠菜单"}
        >
          ☰
        </button>
        <span className="app-header__title">ApiDog</span>
        <div className="app-header__actions">
          {headerAction}
        </div>
      </header>
      <div className="app-body">
      <aside
        className={`sidebar ${sidebarCollapsed ? "sidebar--collapsed" : ""}`}
        aria-hidden={sidebarCollapsed}
      >
        <nav className="sidebar-nav">
          {LINKS.map(({ to, label, short }) => (
            <Link
              key={to}
              to={to}
              className={location.pathname === to ? "active" : ""}
              onClick={closeSidebar}
            >
              <span className="sidebar-nav__full">{label}</span>
              <span className="sidebar-nav__short">{short}</span>
            </Link>
          ))}
        </nav>
      </aside>
      {!sidebarCollapsed && (
        <div
          className="sidebar-overlay"
          onClick={closeSidebar}
          onKeyDown={(e) => e.key === "Escape" && closeSidebar()}
          role="button"
          tabIndex={0}
          aria-label="关闭菜单"
        />
      )}
      <main className="main-inner">
        <Outlet />
      </main>
      </div>
    </div>
    </HeaderActionContext.Provider>
  );
}
