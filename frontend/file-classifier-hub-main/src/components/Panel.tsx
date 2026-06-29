import { cn } from "@/lib/utils";
import { motion } from "framer-motion";
import type { ReactNode } from "react";
import { useSettings } from "@/lib/store";

export function StatCard({
  label, value, icon: Icon, hint, accent = "primary",
}: {
  label: string;
  value: ReactNode;
  icon: any;
  hint?: string;
  accent?: "primary" | "success" | "warning" | "destructive" | "muted";
}) {
  const accentMap = {
    primary: "bg-primary/10 text-primary",
    success: "bg-success/15 text-success",
    warning: "bg-warning/15 text-warning",
    destructive: "bg-destructive/15 text-destructive",
    muted: "bg-muted text-muted-foreground",
  };
  const animations = useSettings((s) => s.animations);
  const body = (
    <div className="rounded-lg border border-border bg-card p-3.5 hover:shadow-sm transition-shadow">
      <div className="flex items-start justify-between">
        <div className="text-[10.5px] font-semibold tracking-wider text-muted-foreground uppercase">{label}</div>
        <div className={cn("w-7 h-7 rounded-md grid place-items-center", accentMap[accent])}>
          <Icon className="w-3.5 h-3.5" />
        </div>
      </div>
      <div className="mt-1.5 text-[22px] font-semibold tabular-nums leading-tight">{value}</div>
      {hint && <div className="text-[11px] text-muted-foreground mt-0.5">{hint}</div>}
    </div>
  );
  if (!animations) return body;
  return (
    <motion.div initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.2 }}>
      {body}
    </motion.div>
  );
}

export function Panel({
  title, description, actions, children, className,
}: {
  title?: string;
  description?: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={cn("rounded-lg border border-border bg-card", className)}>
      {(title || actions) && (
        <header className="flex items-start justify-between gap-3 px-4 py-3 border-b border-border">
          <div>
            {title && <h2 className="text-[13.5px] font-semibold leading-tight">{title}</h2>}
            {description && <p className="text-[11.5px] text-muted-foreground mt-0.5">{description}</p>}
          </div>
          {actions && <div className="flex items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}
