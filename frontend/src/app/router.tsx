import { createBrowserRouter } from "react-router-dom";
import { AppShell } from "@/components/layout/AppShell";
import { DashboardPage } from "@/pages/DashboardPage";
import { StreamsPage } from "@/pages/StreamsPage";
import { NotFoundPage } from "@/pages/NotFoundPage";

/** Central route table. Feature routes (stream detail, etc.) added later. */
export const router = createBrowserRouter([
  {
    path: "/",
    element: <AppShell />,
    children: [
      { index: true, element: <DashboardPage /> },
      { path: "streams", element: <StreamsPage /> },
      { path: "*", element: <NotFoundPage /> },
    ],
  },
]);
