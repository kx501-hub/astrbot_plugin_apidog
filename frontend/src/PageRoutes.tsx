import { Routes, Route } from "react-router-dom";
import App from "./App.tsx";
import Config from "./pages/Config.tsx";
import Apis from "./pages/Apis.tsx";
import Schedules from "./pages/Schedules.tsx";
import Groups from "./pages/Groups.tsx";
import Auth from "./pages/Auth.tsx";

export default function PageRoutes() {
  return (
    <Routes>
      <Route path="/" element={<App />}>
        <Route index element={<Config />} />
        <Route path="apis" element={<Apis />} />
        <Route path="schedules" element={<Schedules />} />
        <Route path="groups" element={<Groups />} />
        <Route path="auth" element={<Auth />} />
      </Route>
    </Routes>
  );
}
