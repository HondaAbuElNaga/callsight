import { useEffect, useRef } from "react";
import * as echarts from "echarts/core";
import { BarChart } from "echarts/charts";
import { GridComponent, LegendComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

// Only what we use (smaller bundle). Canvas renderer: no inline style attributes, so the strict CSP still holds.
echarts.use([BarChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer]);

export const FONT = '"IBM Plex Sans Arabic", "Segoe UI", Tahoma, sans-serif';
export type ChartOption = echarts.EChartsCoreOption;

export function Chart({ option, height = 260, label }: { option: ChartOption; height?: number; label: string }) {
  const box = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    if (!box.current) return;
    const c = echarts.init(box.current, undefined, { renderer: "canvas" });
    chart.current = c;
    const ro = new ResizeObserver(() => c.resize());
    ro.observe(box.current);
    return () => { ro.disconnect(); c.dispose(); chart.current = null; };
  }, []);

  useEffect(() => {
    chart.current?.setOption({ textStyle: { fontFamily: FONT }, ...option }, true);
  }, [option]);

  return <div ref={box} className="chart" style={{ height }} role="img" aria-label={label} />;
}
