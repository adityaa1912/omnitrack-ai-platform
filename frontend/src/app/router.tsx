import { createBrowserRouter } from "react-router-dom";
import { AppShell } from "@/components/layout/AppShell";
import { DashboardPage } from "@/pages/DashboardPage";
import { StreamsPage } from "@/pages/StreamsPage";
import { StreamDetailPage } from "@/pages/StreamDetailPage";
import { NotFoundPage } from "@/pages/NotFoundPage";

/** Central route table. */
export const router = createBrowserRouter([
  {
    path: "/",
    element: <AppShell />,
    children: [
      { index: true, element: <DashboardPage /> },
      { path: "streams", element: <StreamsPage /> },
      { path: "streams/:id", element: <StreamDetailPage /> },
      { path: "*", element: <NotFoundPage /> },
    ],
  },
]);
