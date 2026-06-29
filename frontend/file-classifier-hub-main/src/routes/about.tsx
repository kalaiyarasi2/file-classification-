import { createFileRoute } from "@tanstack/react-router";
import { Info, FileText, Tags, Workflow, Cpu, LayoutDashboard, Shield } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { Panel } from "@/components/Panel";

export const Route = createFileRoute("/about")({ component: AboutPage });

const FEATURES = [
  { icon: FileText, title: "Text Extraction", body: "Pull text with OCR & auto-rotation when needed." },
  { icon: Tags, title: "Classification", body: "Score documents against your configured categories." },
  { icon: Workflow, title: "Pipeline", body: "End-to-end batch processing with reports." },
  { icon: Cpu, title: "LLM Powered", body: "Override the LLM model per request as needed." },
  { icon: LayoutDashboard, title: "Enterprise UI", body: "Compact, accessible, theme-aware dashboard." },
];

const STACK = ["React", "TanStack Router", "TypeScript", "TailwindCSS", "shadcn/ui", "Framer Motion", "Recharts", "Lucide Icons"];

function AboutPage() {
  return (
    <>
      <PageHeader icon={Info} title="About" description="File Classifier Agent — an AI-powered document classification platform." />

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
        <Panel title="Overview" description="What this dashboard does" className="lg:col-span-2">
          <p className="text-[12.5px] leading-relaxed text-muted-foreground">
            File Classifier Agent integrates with a FastAPI backend that detects PDF type, extracts text
            (with optional OCR and auto-rotation), classifies documents against configurable categories using
            an LLM, organises files into category folders and produces a CSV report. The dashboard exposes
            every backend endpoint as a dedicated workflow.
          </p>
          <div className="flex flex-wrap gap-1.5 mt-3">
            {STACK.map((t) => (
              <span key={t} className="text-[11px] px-2 py-0.5 rounded bg-muted font-mono">{t}</span>
            ))}
          </div>
        </Panel>

        <Panel title="Application">
          <div className="flex items-center gap-3 mb-3">
            <div className="w-10 h-10 rounded-md bg-primary/10 text-primary grid place-items-center">
              <Shield className="w-5 h-5" />
            </div>
            <div>
              <div className="text-[13px] font-semibold">File Classifier Agent</div>
              <div className="text-[11px] text-muted-foreground">v1.0.0 · Enterprise edition</div>
            </div>
          </div>
          <div className="text-[11px] text-muted-foreground">© 2026 File Classifier Agent</div>
          <div className="text-[11px] text-muted-foreground mt-1">License: Proprietary</div>
        </Panel>

        <Panel title="Features" className="lg:col-span-3">
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {FEATURES.map((f) => (
              <div key={f.title} className="rounded-md border border-border p-3 flex items-start gap-3">
                <div className="w-8 h-8 rounded-md bg-primary/10 text-primary grid place-items-center shrink-0">
                  <f.icon className="w-4 h-4" />
                </div>
                <div>
                  <div className="text-[12.5px] font-semibold">{f.title}</div>
                  <div className="text-[11.5px] text-muted-foreground mt-0.5">{f.body}</div>
                </div>
              </div>
            ))}
          </div>
        </Panel>
      </div>
    </>
  );
}
