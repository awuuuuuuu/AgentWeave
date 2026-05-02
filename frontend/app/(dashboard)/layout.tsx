import NavHeader from "@/components/nav-header";

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="h-screen flex flex-col bg-background">
      <NavHeader />
      <main className="flex-1 overflow-auto">
        {children}
      </main>
    </div>
  );
}
