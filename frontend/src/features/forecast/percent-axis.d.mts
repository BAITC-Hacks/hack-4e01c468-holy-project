export type PercentAxisDomain = [number, number];
export type PercentAxisMode = 'auto' | 'manual' | 'full';
export interface PercentAxisState {
  mode: PercentAxisMode;
  domain: PercentAxisDomain | null;
}

export function autoPercentDomain(rows: readonly Record<string, unknown>[]): PercentAxisDomain;
export function createPercentAxisState(): PercentAxisState;
export function resolvePercentAxisDomain(state: PercentAxisState, rows: readonly Record<string, unknown>[]): PercentAxisDomain;
export function reducePercentAxisState(state: PercentAxisState, action: string, rows: readonly Record<string, unknown>[]): PercentAxisState;
export function percentAxisTicks(domain: PercentAxisDomain, targetIntervals?: number): number[];
