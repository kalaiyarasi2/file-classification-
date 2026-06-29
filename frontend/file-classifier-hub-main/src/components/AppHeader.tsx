import { Bell, Moon, Sun, Search, ChevronDown, Settings2, Info, User, LogOut, PanelLeft } from "lucide-react";
import { useSettings, useApp } from "@/lib/store";
import { useNavigate } from "@tanstack/react-router";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem,
  DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { ScrollArea } from "@/components/ui/scroll-area";

export function AppHeader({ onToggleSidebar }: { onToggleSidebar: () => void }) {
  const { theme, setTheme } = useSettings();
  const navigate = useNavigate();
  const activity = useApp((s) => s.activity);

  return (
    <header className="h-14 shrink-0 border-b border-border bg-card/60 backdrop-blur-sm flex items-center gap-3 px-4">
      <button
        onClick={onToggleSidebar}
        className="p-1.5 rounded hover:bg-accent text-muted-foreground"
        aria-label="Toggle sidebar"
      >
        <PanelLeft className="w-4 h-4" />
      </button>

      <div className="flex items-center gap-2">
        <span className="text-[13px] font-semibold">File Classifier Agent</span>
        <span className="text-[10px] px-1.5 py-0.5 rounded bg-primary/10 text-primary font-medium tracking-wider">
          ENTERPRISE
        </span>
      </div>

      <div className="flex-1 max-w-xl mx-auto relative">
        <Search className="w-3.5 h-3.5 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
        <input
          type="text"
          placeholder="Search files, categories, reports..."
          className="w-full h-8 pl-8 pr-3 text-[12.5px] rounded-md border border-input bg-background placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring/40"
        />
      </div>

      <button
        onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
        className="p-1.5 rounded hover:bg-accent text-muted-foreground"
        aria-label="Toggle theme"
      >
        {theme === "dark" ? <Sun className="w-4 h-4" /> : <Moon className="w-4 h-4" />}
      </button>

      <Popover>
        <PopoverTrigger asChild>
          <button className="relative p-1.5 rounded hover:bg-accent text-muted-foreground" aria-label="Notifications">
            <Bell className="w-4 h-4" />
            {activity.length > 0 && (
              <span className="absolute top-0.5 right-0.5 min-w-[14px] h-[14px] px-1 text-[9px] font-bold rounded-full bg-primary text-primary-foreground grid place-items-center">
                {activity.length > 9 ? "9+" : activity.length}
              </span>
            )}
          </button>
        </PopoverTrigger>
        <PopoverContent align="end" className="w-80 p-0">
          <div className="p-3 border-b border-border">
            <div className="text-[13px] font-semibold">Recent Activity</div>
            <div className="text-[11px] text-muted-foreground">{activity.length} events</div>
          </div>
          <ScrollArea className="h-72">
            {activity.length === 0 ? (
              <div className="p-6 text-center text-[12px] text-muted-foreground">No recent activity</div>
            ) : (
              <ul className="divide-y divide-border">
                {activity.slice(0, 10).map((a) => (
                  <li key={a.id} className="p-3">
                    <div className="text-[12.5px] font-medium truncate">{a.title}</div>
                    <div className="text-[11px] text-muted-foreground truncate">{a.detail}</div>
                    <div className="text-[10px] text-muted-foreground mt-1">{new Date(a.ts).toLocaleString()}</div>
                  </li>
                ))}
              </ul>
            )}
          </ScrollArea>
        </PopoverContent>
      </Popover>

      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button className="flex items-center gap-2 pl-1 pr-2 py-1 rounded hover:bg-accent">
            <div className="w-7 h-7 rounded-full bg-primary/10 text-primary grid place-items-center text-[11px] font-bold">
              FC
            </div>
            <span className="text-[12.5px] font-medium">Admin</span>
            <ChevronDown className="w-3.5 h-3.5 text-muted-foreground" />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-52">
          <DropdownMenuLabel className="text-[11px] text-muted-foreground font-normal">
            Signed in as <span className="font-medium text-foreground">admin@local</span>
          </DropdownMenuLabel>
          <DropdownMenuSeparator />
          <DropdownMenuItem onClick={() => navigate({ to: "/settings" })}>
            <Settings2 className="w-3.5 h-3.5" /> Settings
          </DropdownMenuItem>
          <DropdownMenuItem onClick={() => navigate({ to: "/about" })}>
            <Info className="w-3.5 h-3.5" /> About
          </DropdownMenuItem>
          <DropdownMenuItem>
            <User className="w-3.5 h-3.5" /> Profile
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem className="text-destructive">
            <LogOut className="w-3.5 h-3.5" /> Sign out
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </header>
  );
}
