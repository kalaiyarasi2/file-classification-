import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { Settings2, Save, RotateCcw, Moon, Sun, Globe, Server, FolderInput, FolderOutput } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { Panel } from "@/components/Panel";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { useSettings } from "@/lib/store";
import { getBackendUrl, setBackendUrl } from "@/lib/api";
import { toast } from "sonner";

export const Route = createFileRoute("/settings")({ component: SettingsPage });

function SettingsPage() {
  const s = useSettings();
  const [backend, setBackend] = useState(getBackendUrl());

  function reset() {
    s.setTheme("light"); s.setAnimations(true); s.setLanguage("English");
    s.setDefaultInputFolder("/data/incoming"); s.setDefaultOutputFolder("/data/sorted");
    setBackendUrl("http://127.0.0.1:8000"); setBackend("http://127.0.0.1:8000");
    toast.success("Reset to defaults");
  }

  return (
    <>
      <PageHeader
        icon={Settings2}
        title="Settings"
        description="Personalise the dashboard. Preferences are stored locally on this browser."
        actions={<Button size="sm" variant="outline" onClick={reset}><RotateCcw className="w-3.5 h-3.5" /> Reset to defaults</Button>}
      />

      <div className="space-y-3">
        <Panel title="Appearance" description="Theme and motion">
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div>
              <Label className="text-[11.5px]">Theme</Label>
              <div className="flex gap-1 mt-1">
                <Button size="sm" variant={s.theme === "light" ? "default" : "outline"} onClick={() => s.setTheme("light")}>
                  <Sun className="w-3.5 h-3.5" /> Light
                </Button>
                <Button size="sm" variant={s.theme === "dark" ? "default" : "outline"} onClick={() => s.setTheme("dark")}>
                  <Moon className="w-3.5 h-3.5" /> Dark
                </Button>
              </div>
            </div>
            <div className="flex items-end gap-2 pb-1.5">
              <Switch checked={s.animations} onCheckedChange={s.setAnimations} />
              <Label className="text-[11.5px]">Animations</Label>
            </div>
            <div>
              <Label className="text-[11.5px] flex items-center gap-1"><Globe className="w-3 h-3" /> Language</Label>
              <select value={s.language} onChange={(e) => s.setLanguage(e.target.value)} className="w-full h-8 mt-1 px-2 rounded-md border border-input bg-background text-[12.5px]">
                <option>English</option><option>Français</option><option>Español</option><option>Deutsch</option>
              </select>
            </div>
          </div>
        </Panel>

        <Panel title="Backend" description="Override the FastAPI base URL used by the dashboard">
          <div className="flex flex-wrap items-end gap-3">
            <div className="flex-1 min-w-[260px]">
              <Label className="text-[11.5px] flex items-center gap-1"><Server className="w-3 h-3" /> Backend URL</Label>
              <Input value={backend} onChange={(e) => setBackend(e.target.value)} className="h-8 mt-1 font-mono text-[12px]" />
              <p className="text-[10.5px] text-muted-foreground mt-1">Endpoints are called without an /api prefix.</p>
            </div>
            <Button size="sm" onClick={() => { setBackendUrl(backend); toast.success("Backend URL saved"); }}>
              <Save className="w-3.5 h-3.5" /> Save
            </Button>
          </div>
        </Panel>

        <Panel title="Default Folders" description="Pre-filled across Pipeline & Drive pages">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <Label className="text-[11.5px] flex items-center gap-1"><FolderInput className="w-3 h-3" /> Default Upload Folder</Label>
              <Input value={s.defaultInputFolder} onChange={(e) => s.setDefaultInputFolder(e.target.value)} className="h-8 mt-1 font-mono text-[12px]" />
            </div>
            <div>
              <Label className="text-[11.5px] flex items-center gap-1"><FolderOutput className="w-3 h-3" /> Default Output Folder</Label>
              <Input value={s.defaultOutputFolder} onChange={(e) => s.setDefaultOutputFolder(e.target.value)} className="h-8 mt-1 font-mono text-[12px]" />
            </div>
          </div>
        </Panel>
      </div>
    </>
  );
}
