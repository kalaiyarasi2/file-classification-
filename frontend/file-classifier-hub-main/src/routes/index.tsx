import { createFileRoute } from "@tanstack/react-router";
import { useNavigate } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import {
  LayoutDashboard, Files, CheckCircle2, ScanLine, FileText, ScanSearch,
  Tags, XCircle, Layers, Timer, Workflow, Sparkles,
} from "lucide-react";
import {
  BarChart, Bar, XAxis, YAxis, ResponsiveContainer, Tooltip, CartesianGrid,
  LineChart, Line, PieChart, Pie, Cell, Legend,
} from "recharts";
import { PageHeader } from "@/components/PageHeader";
import { Panel, StatCard } from "@/components/Panel";
import { Button } from "@/components/ui/button";
import { useApp } from "@/lib/store";
import { api } from "@/lib/api";

export const Route = createFileRoute("/")({ component: Dashboard });

const CHART_COLORS = ["#3b82f6", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6", "#06b6d4", "#ec4899"];

function Dashboard() {
  const stats = useApp((s) => s.stats);
  const navigate = useNavigate();

  const { data: health } = useQuery({
    queryKey: ["health"], queryFn: api.health, retry: false, refetchInterval: 30000,
  });

  const avgMs = stats.processed ? Math.round(stats.totalProcessingMs / stats.processed) : 0;
  const catData = Object.entries(stats.categoriesFound).map(([name, value]) => ({ name, value }));
  const dailyData = stats.daily.length
    ? stats.daily
    : ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((d) => ({ day: d, processed: 0, failed: 0 }));
  const confData = ["0-2", "2-4", "4-6", "6-7", "7-8", "8-9", "9-10"].map((b, i) => ({
    bucket: b, count: stats.confidenceBuckets[i] || 0,
  }));

  return (
    <>
      <PageHeader
        icon={LayoutDashboard}
        title="Dashboard"
        description="Overview of document classification activity, pipeline performance and system health."
        actions={
          <Button size="sm" onClick={() => navigate({ to: "/classification" })}>
            <Sparkles className="w-3.5 h-3.5" /> New Classification
          </Button>
        }
      />

      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3 mb-4">
        <StatCard label="Total Files" value={stats.totalFiles} icon={Files} hint="Tracked this session" />
        <StatCard label="Processed" value={stats.processed} icon={CheckCircle2} accent="success" />
        <StatCard label="Scanned PDFs" value={stats.scanned} icon={ScanLine} accent="warning" />
        <StatCard label="Digital PDFs" value={stats.digital} icon={FileText} />
        <StatCard label="OCR Processed" value={stats.ocrProcessed} icon={ScanSearch} accent="muted" />
        <StatCard label="Classification Success" value={stats.classificationSuccess} icon={Tags} accent="success" />
        <StatCard label="Failures" value={stats.failures} icon={XCircle} accent="destructive" />
        <StatCard label="Categories Found" value={Object.keys(stats.categoriesFound).length} icon={Layers} />
        <StatCard label="Avg Processing" value={avgMs ? `${avgMs} ms` : "—"} icon={Timer} accent="muted" />
        <StatCard label="Pipeline" value={stats.pipelineRuns || "—"} icon={Workflow} accent={health?.status === "ok" ? "success" : "muted"} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
        <Panel title="Category Distribution" description="Share of classified documents by category">
          {catData.length === 0 ? (
            <div className="h-56 grid place-items-center text-[12.5px] text-muted-foreground">No data yet.</div>
          ) : (
            <div className="h-56">
              <ResponsiveContainer>
                <PieChart>
                  <Pie data={catData} dataKey="value" nameKey="name" outerRadius={70} innerRadius={40} paddingAngle={2}>
                    {catData.map((_, i) => <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />)}
                  </Pie>
                  <Tooltip contentStyle={{ fontSize: 12, borderRadius: 6 }} />
                  <Legend wrapperStyle={{ fontSize: 11 }} />
                </PieChart>
              </ResponsiveContainer>
            </div>
          )}
        </Panel>

        <Panel title="Daily Processing" description="Last 7 days · processed vs failed">
          <div className="h-56">
            <ResponsiveContainer>
              <BarChart data={dailyData}>
                <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
                <XAxis dataKey="day" fontSize={10} tickFormatter={(v: string) => v.length > 3 ? v.slice(5) : v} />
                <YAxis fontSize={10} />
                <Tooltip contentStyle={{ fontSize: 12, borderRadius: 6 }} />
                <Bar dataKey="processed" fill="hsl(217 91% 60%)" radius={[2, 2, 0, 0]} />
                <Bar dataKey="failed" fill="hsl(0 84% 60%)" radius={[2, 2, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Panel>

        <Panel title="Confidence Distribution" description="LLM score buckets across runs">
          <div className="h-56">
            <ResponsiveContainer>
              <LineChart data={confData}>
                <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
                <XAxis dataKey="bucket" fontSize={10} />
                <YAxis fontSize={10} />
                <Tooltip contentStyle={{ fontSize: 12, borderRadius: 6 }} />
                <Line type="monotone" dataKey="count" stroke="hsl(217 91% 60%)" strokeWidth={2} dot={{ r: 3 }} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </Panel>
      </div>
    </>
  );
}
