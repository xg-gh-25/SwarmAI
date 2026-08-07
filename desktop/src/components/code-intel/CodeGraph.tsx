/**
 * CodeGraph — Force-directed visualization of the code intelligence graph.
 *
 * Shows top-N most-connected symbols as nodes, colored by module.
 * Interactive: hover shows name, click focuses neighborhood, scroll zooms.
 * Dark theme matching the app aesthetic.
 *
 * Uses react-force-graph-2d for zero-config force-directed layout.
 */

import { useEffect, useState, useCallback, useRef, useMemo } from 'react';
import ForceGraph2D from 'react-force-graph-2d';
import { getCodeIntelGraph, type GraphNode } from '../../services/codeIntel';

// ── Module color palette (deterministic hash → color) ─────────────────────────

const MODULE_COLORS = [
  '#6366f1', // indigo
  '#ec4899', // pink
  '#14b8a6', // teal
  '#f59e0b', // amber
  '#8b5cf6', // violet
  '#10b981', // emerald
  '#f43f5e', // rose
  '#06b6d4', // cyan
  '#84cc16', // lime
  '#f97316', // orange
  '#a855f7', // purple
  '#22d3ee', // sky
];

function getModuleColor(module: string): string {
  let hash = 0;
  for (let i = 0; i < module.length; i++) {
    hash = module.charCodeAt(i) + ((hash << 5) - hash);
  }
  return MODULE_COLORS[Math.abs(hash) % MODULE_COLORS.length];
}

// ── Node type shapes ──────────────────────────────────────────────────────────

const TYPE_SIZE: Record<string, number> = {
  class: 6,
  function: 4,
  method: 3,
  variable: 2,
};

// ── Component ─────────────────────────────────────────────────────────────────

interface CodeGraphProps {
  project: string;
  limit?: number;
  onClose?: () => void;
  /** inline=true → embed in a parent pane (`relative h-full w-full`) instead of the
   *  default fullscreen overlay (`fixed inset-0 z-50`). ADDITIVE: omitting it keeps
   *  the byte-identical fullscreen shape the shared callers (BottomBar, legacy
   *  "View code graph") depend on. The Brain-Hub detail pane passes inline. */
  inline?: boolean;
}

interface ForceGraphNode extends GraphNode {
  x?: number;
  y?: number;
  vx?: number;
  vy?: number;
  __indexColor?: string;
}

interface ForceGraphLink {
  source: string | ForceGraphNode;
  target: string | ForceGraphNode;
  type: string;
}

export function CodeGraph({ project, limit = 300, onClose, inline = false }: CodeGraphProps) {
  // Outer container: fullscreen overlay by default; parent-filling when embedded.
  // The `bg-[#0d1117]` is shared; only the positioning differs (Gate-1 B1: the
  // inline parent — BrainHub's flex-1 content pane — has a definite height, so
  // h-full + the ResizeObserver measure non-zero).
  const outerCls = inline ? 'relative h-full w-full bg-[#0d1117]' : 'fixed inset-0 z-50 bg-[#0d1117]';
  const centerCls = `${outerCls} flex items-center justify-center`;
  const [graphData, setGraphData] = useState<{ nodes: ForceGraphNode[]; links: ForceGraphLink[] } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [hoveredNode, setHoveredNode] = useState<ForceGraphNode | null>(null);
  const [focusedNode, setFocusedNode] = useState<ForceGraphNode | null>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any -- ForceGraph2D ref type requires complex generics
  const graphRef = useRef<any>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [dimensions, setDimensions] = useState({ width: 800, height: 600 });

  // Fetch graph data
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    getCodeIntelGraph(project, limit)
      .then((data) => {
        if (cancelled) return;
        if (!data) {
          setError('No code intelligence data found');
          return;
        }
        // Transform to force-graph format (edges → links)
        setGraphData({
          nodes: data.nodes as ForceGraphNode[],
          links: data.edges.map(e => ({ source: e.source, target: e.target, type: e.type })),
        });
      })
      .catch((err) => {
        if (!cancelled) setError(err.message || 'Failed to load graph');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => { cancelled = true; };
  }, [project, limit]);

  // Responsive sizing
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        setDimensions({
          width: entry.contentRect.width,
          height: entry.contentRect.height,
        });
      }
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Focus on node click (zoom to neighborhood)
  const handleNodeClick = useCallback((node: ForceGraphNode) => {
    if (graphRef.current) {
      graphRef.current.centerAt(node.x, node.y, 400);
      graphRef.current.zoom(3, 400);
    }
    setFocusedNode(node);
  }, []);

  // Double-click to reset view
  const handleBackgroundClick = useCallback(() => {
    if (graphRef.current) {
      graphRef.current.zoomToFit(400, 40);
    }
    setFocusedNode(null);
  }, []);

  // Module legend (deduplicated from actual data)
  const modules = useMemo(() => {
    if (!graphData) return [];
    const counts: Record<string, number> = {};
    for (const node of graphData.nodes) {
      counts[node.module] = (counts[node.module] || 0) + 1;
    }
    return Object.entries(counts)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 10);
  }, [graphData]);

  // Node painting
  const paintNode = useCallback((node: ForceGraphNode, ctx: CanvasRenderingContext2D) => {
    const size = TYPE_SIZE[node.type] || 3;
    const color = getModuleColor(node.module);
    const isFocused = focusedNode?.id === node.id;
    const isHovered = hoveredNode?.id === node.id;

    ctx.beginPath();
    ctx.arc(node.x!, node.y!, size * (isFocused || isHovered ? 1.5 : 1), 0, 2 * Math.PI);
    ctx.fillStyle = isFocused ? '#ffffff' : color;
    ctx.fill();

    if (isHovered || isFocused) {
      ctx.strokeStyle = '#ffffff';
      ctx.lineWidth = 1;
      ctx.stroke();
    }
  }, [focusedNode, hoveredNode]);

  if (loading) {
    return (
      <div className={centerCls}>
        <div className="text-[var(--color-text-muted)] text-sm">Loading code graph...</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className={centerCls}>
        <div className="text-red-400 text-sm">{error}</div>
        {onClose && <button onClick={onClose} className="absolute top-4 right-4 text-gray-400 hover:text-white">Close</button>}
      </div>
    );
  }

  if (!graphData || graphData.nodes.length === 0) {
    return (
      <div className={centerCls}>
        <div className="text-gray-400 text-sm">No code symbols indexed yet. Run a re-index first.</div>
        {onClose && <button onClick={onClose} className="absolute top-4 right-4 text-gray-400 hover:text-white text-sm">Close</button>}
      </div>
    );
  }

  return (
    <div className={outerCls} ref={containerRef}>
      {/* Header */}
      <div className="absolute top-0 left-0 right-0 h-10 flex items-center justify-between px-4 bg-[#0d1117]/80 backdrop-blur-sm border-b border-gray-800 z-10">
        <div className="flex items-center gap-3">
          <span className="text-sm font-medium text-gray-200">Code Intelligence Graph</span>
          <span className="text-xs text-gray-500">{project}</span>
          <span className="text-xs text-gray-600">{graphData.nodes.length} nodes / {graphData.links.length} edges</span>
        </div>
        {onClose && (
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-white text-sm px-2 py-1 rounded hover:bg-gray-800"
          >
            ESC
          </button>
        )}
      </div>

      {/* Legend */}
      <div className="absolute bottom-4 left-4 bg-[#161b22] border border-gray-800 rounded-lg p-3 z-10 max-w-[200px]">
        <div className="text-[10px] text-gray-400 mb-2 font-medium">Modules</div>
        {modules.map(([mod, count]) => (
          <div key={mod} className="flex items-center gap-2 text-[10px] text-gray-300 py-0.5">
            <div className="w-2.5 h-2.5 rounded-full flex-shrink-0" style={{ backgroundColor: getModuleColor(mod) }} />
            <span className="truncate">{mod}</span>
            <span className="text-gray-600 ml-auto">{count}</span>
          </div>
        ))}
      </div>

      {/* Hover tooltip */}
      {hoveredNode && (
        <div className="absolute top-12 left-1/2 -translate-x-1/2 bg-[#161b22] border border-gray-700 rounded px-3 py-1.5 z-10 pointer-events-none">
          <div className="text-xs text-white font-mono">{hoveredNode.name}</div>
          <div className="text-[10px] text-gray-400">{hoveredNode.type} · {hoveredNode.file_path}</div>
        </div>
      )}

      {/* Graph */}
      <ForceGraph2D
        ref={graphRef}
        width={dimensions.width}
        height={dimensions.height}
        graphData={graphData}
        nodeCanvasObject={paintNode}
        nodePointerAreaPaint={(node: ForceGraphNode, color: string, ctx: CanvasRenderingContext2D) => {
          const size = TYPE_SIZE[node.type] || 3;
          ctx.beginPath();
          ctx.arc(node.x!, node.y!, size * 2, 0, 2 * Math.PI);
          ctx.fillStyle = color;
          ctx.fill();
        }}
        linkColor={() => 'rgba(99, 102, 241, 0.15)'}
        linkWidth={0.5}
        onNodeHover={(node: ForceGraphNode | null) => setHoveredNode(node)}
        onNodeClick={handleNodeClick}
        onBackgroundClick={handleBackgroundClick}
        backgroundColor="#0d1117"
        cooldownTicks={100}
        warmupTicks={50}
      />
    </div>
  );
}
