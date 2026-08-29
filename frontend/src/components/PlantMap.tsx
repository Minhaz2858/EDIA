import React from 'react';

export const PlantMap: React.FC = () => {
  return (
    <div className="relative h-full w-full rounded-xl overflow-hidden border border-slate-200 bg-[radial-gradient(circle_at_top,_#f8fafc,_#e2e8f0)]">
      <div className="absolute inset-0 bg-[linear-gradient(120deg,rgba(148,163,184,0.08)_0%,rgba(255,255,255,0.25)_45%,rgba(148,163,184,0.08)_100%)]" />
      <div className="absolute inset-0 flex items-center justify-center bg-white/55 backdrop-blur-[1px]">
        <div className="rounded-lg border border-slate-200 bg-white px-4 py-3 text-center shadow-sm">
          <div className="text-sm font-semibold text-slate-700">Telemetry Source Not Connected</div>
          <div className="mt-1 text-xs text-slate-500">The plant status map will remain blank until a real telemetry feed is available.</div>
        </div>
      </div>
    </div>
  );
};
