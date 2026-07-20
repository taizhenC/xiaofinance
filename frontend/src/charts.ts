/** ECharts, tree-shaken: only the pieces this dashboard renders. The version
 * is pinned exactly in package.json + lockfile and bundled at build time —
 * no CDN, no "latest", identical on every install (PL-05). */

import * as echarts from "echarts/core";
import { BarChart, LineChart, PieChart } from "echarts/charts";
import { GridComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import { useEffect, useRef } from "preact/hooks";
import { theme } from "./theme";

echarts.use([BarChart, LineChart, PieChart, GridComponent, TooltipComponent, CanvasRenderer]);

export { echarts };

export interface ChartTokens {
  bull: string;
  bear: string;
  neut: string;
  accent: string;
  amber: string;
  ink: string;
  ink3: string;
  grid: string;
  panel2: string;
  mono: string;
}

/** Chart colors come from the live CSS custom properties, so charts follow
 * the light/dark theme exactly like the rest of the page. */
export function chartTokens(): ChartTokens {
  const css = getComputedStyle(document.documentElement);
  const v = (name: string) => css.getPropertyValue(name).trim();
  return {
    bull: v("--bull"), bear: v("--bear"), neut: v("--neut"),
    accent: v("--accent"), amber: v("--amber"),
    ink: v("--ink"), ink3: v("--ink-3"), grid: v("--border"),
    panel2: v("--panel-2"), mono: v("--mono"),
  };
}

/** Mount an ECharts instance on a div; re-renders when deps change or the
 * theme flips; disposes on unmount; resizes with the window. */
export function useChart(
  build: (tokens: ChartTokens) => echarts.EChartsCoreOption | null,
  deps: unknown[],
) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const option = build(chartTokens());
    if (option == null) {
      chartRef.current?.dispose();
      chartRef.current = null;
      return;
    }
    if (!chartRef.current) {
      chartRef.current = echarts.init(el, undefined, { renderer: "canvas" });
    }
    chartRef.current.setOption(option, true);

    const onResize = () => chartRef.current?.resize();
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, theme.value]);

  useEffect(() => () => {
    chartRef.current?.dispose();
    chartRef.current = null;
  }, []);

  return { ref, chartRef };
}
