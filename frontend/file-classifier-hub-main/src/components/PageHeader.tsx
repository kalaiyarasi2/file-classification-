import { Link, useRouterState } from "@tanstack/react-router";
import { Home, ChevronRight } from "lucide-react";
import { motion } from "framer-motion";
import { useSettings } from "@/lib/store";
import type { ReactNode } from "react";

const PRETTY: Record<string, string> = {
  "": "Dashboard",
  upload: "Upload",
  detection: "PDF Detection",
  extraction: "Text Extraction",
  classification: "Classification",
  pipeline: "Pipeline",
  organisation: "File Organisation",
  drive: "Google Drive",
  reports: "Reports",
  health: "System Health",
  logs: "Logs",
  configuration: "Configuration",
  settings: "Settings",
  about: "About",
};

export function PageHeader({
  icon: Icon,
  title,
  description,
  actions,
}: {
  icon: any;
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const seg = pathname.replace(/^\//, "");
  const crumb = PRETTY[seg] || title;
  const animations = useSettings((s) => s.animations);

  const content = (
    <div className="mb-5">
      <div className="flex items-center gap-1.5 text-[11.5px] text-muted-foreground mb-2">
        <Link to="/" className="flex items-center gap-1 hover:text-foreground">
          <Home className="w-3 h-3" /> Home
        </Link>
        <ChevronRight className="w-3 h-3" />
        <span className="text-foreground">{crumb}</span>
      </div>
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div className="flex items-start gap-3">
          <div className="w-9 h-9 rounded-md bg-primary/10 text-primary grid place-items-center shrink-0">
            <Icon className="w-[18px] h-[18px]" />
          </div>
          <div>
            <h1 className="text-[20px] font-semibold leading-tight tracking-tight">{title}</h1>
            {description && (
              <p className="text-[12.5px] text-muted-foreground mt-0.5 max-w-2xl">{description}</p>
            )}
          </div>
        </div>
        {actions && <div className="flex items-center gap-2">{actions}</div>}
      </div>
    </div>
  );

  if (!animations) return content;
  return (
    <motion.div initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.2 }}>
      {content}
    </motion.div>
  );
}
