import { Outlet } from 'react-router-dom';
import { getCurrentWindow } from '@tauri-apps/api/window';
import Sidebar from './Sidebar';

export default function Layout() {
  const handleMouseDown = async (e: React.MouseEvent) => {
    // Only start drag on left mouse button and not on traffic light area
    if (e.button === 0 && e.clientX > 80) {
      try {
        await getCurrentWindow().startDragging();
      } catch (err) {
        console.error('Failed to start dragging:', err);
      }
    }
  };

  return (
    <div className="flex flex-col h-screen bg-dark-bg">
      {/* Top bar with traffic lights area - draggable */}
      <div
        onMouseDown={handleMouseDown}
        className="h-10 bg-dark-bg border-b border-dark-border flex-shrink-0 select-none cursor-default"
      />

      {/* Main layout below top bar */}
      <div className="flex flex-1 overflow-hidden">
        {/* Always show collapsed icon sidebar */}
        <Sidebar collapsed />

        {/* Main content */}
        <main className="flex-1 overflow-auto">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
