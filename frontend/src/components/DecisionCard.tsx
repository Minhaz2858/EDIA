import React from "react";
import {
  TrendingUp,
  TrendingDown,
  AlertTriangle,
  CheckCircle,
  ArrowRight,
  Eye,
  Zap,
} from "lucide-react";

export interface Trigger {
  condition: string;
  action: string;
  probability?: string;
}

export interface PricingDecision {
  product_id: string;
  product_name?: string;
  recommended_price_cny_ton: number | null;
  price_range: [number, number];
  action: string;
  action_bias: string;
  confidence?: number;
  adjustment_reasons?: string[];
  triggers?: Trigger[];
  watch_signals?: string[];
  dissenting_view?: string;
  override_applied?: boolean;
  override_note?: string;
}

const ACTION_CONFIG: Record<
  string,
  { label: string; color: string; bg: string; icon: React.ReactNode }
> = {
  raise_price: {
    label: "Raise Price",
    color: "text-emerald-700",
    bg: "bg-emerald-50 border-emerald-200",
    icon: <TrendingUp size={16} />,
  },
  hold_price: {
    label: "Hold Price",
    color: "text-blue-700",
    bg: "bg-blue-50 border-blue-200",
    icon: <ArrowRight size={16} />,
  },
  lower_price: {
    label: "Lower Price",
    color: "text-red-700",
    bg: "bg-red-50 border-red-200",
    icon: <TrendingDown size={16} />,
  },
  accelerate_shipments_with_discount: {
    label: "Accelerate Shipments",
    color: "text-amber-700",
    bg: "bg-amber-50 border-amber-200",
    icon: <Zap size={16} />,
  },
  flexible_pricing: {
    label: "Flexible Pricing",
    color: "text-purple-700",
    bg: "bg-purple-50 border-purple-200",
    icon: <Eye size={16} />,
  },
};

const BIAS_BADGE: Record<string, { label: string; cls: string }> = {
  biased_upward: { label: "Biased Upward ↗", cls: "bg-emerald-100 text-emerald-800" },
  accelerate_shipments: { label: "Accelerate Shipments", cls: "bg-amber-100 text-amber-800" },
  flexible_pricing: { label: "Flexible Pricing", cls: "bg-purple-100 text-purple-800" },
  watchful_hold: { label: "Watchful Hold", cls: "bg-gray-100 text-gray-700" },
  neutral: { label: "Neutral", cls: "bg-gray-100 text-gray-600" },
};

export const DecisionCard: React.FC<{ decision: PricingDecision }> = ({
  decision,
}) => {
  const actionCfg = ACTION_CONFIG[decision.action] ?? {
    label: decision.action.replace(/_/g, " "),
    color: "text-gray-700",
    bg: "bg-gray-50 border-gray-200",
    icon: <ArrowRight size={16} />,
  };

  const biasBadge = BIAS_BADGE[decision.action_bias] ?? {
    label: decision.action_bias,
    cls: "bg-gray-100 text-gray-600",
  };

  const [lo, hi] = decision.price_range ?? [0, 0];

  return (
    <div className={`rounded-lg border p-4 shadow-sm ${actionCfg.bg}`}>
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <span className={actionCfg.color}>{actionCfg.icon}</span>
          <h3 className="font-semibold text-base">
            {decision.product_name ?? decision.product_id}
          </h3>
        </div>
        <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${biasBadge.cls}`}>
          {biasBadge.label}
        </span>
      </div>

      {/* Price row */}
      <div className="flex items-baseline gap-4 mb-3">
        {decision.recommended_price_cny_ton != null ? (
          <span className="text-2xl font-bold">
            ¥{decision.recommended_price_cny_ton.toLocaleString()}
          </span>
        ) : (
          <span className="text-lg font-semibold text-gray-500">Range-based</span>
        )}
        <span className="text-sm text-gray-500">
          Range: ¥{lo.toLocaleString()} – ¥{hi.toLocaleString()} /ton
        </span>
        {decision.confidence != null && (
          <span className="text-xs text-gray-400">
            Confidence: {(decision.confidence * 100).toFixed(0)}%
          </span>
        )}
      </div>

      {/* Action label */}
      <div className={`text-sm font-medium mb-3 ${actionCfg.color}`}>
        {actionCfg.label}
      </div>

      {/* Override note */}
      {decision.override_applied && decision.override_note && (
        <div className="flex items-start gap-2 bg-yellow-50 border border-yellow-200 rounded p-2 mb-3 text-xs text-yellow-800">
          <AlertTriangle size={14} className="mt-0.5 flex-shrink-0" />
          <span>{decision.override_note}</span>
        </div>
      )}

      {/* Adjustment reasons */}
      {decision.adjustment_reasons && decision.adjustment_reasons.length > 0 && (
        <div className="mb-3">
          <p className="text-xs font-semibold text-gray-500 mb-1">Adjustment Reasons</p>
          <ul className="space-y-1">
            {decision.adjustment_reasons.map((r, i) => (
              <li key={i} className="flex items-start gap-1.5 text-xs text-gray-700">
                <CheckCircle size={12} className="mt-0.5 text-green-500 flex-shrink-0" />
                {r}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Triggers */}
      {decision.triggers && decision.triggers.length > 0 && (
        <div className="mb-3">
          <p className="text-xs font-semibold text-gray-500 mb-1">Conditional Triggers</p>
          <div className="space-y-1">
            {decision.triggers.map((t, i) => (
              <div key={i} className="flex items-start gap-2 text-xs bg-white/60 rounded p-1.5">
                <Zap size={12} className="mt-0.5 text-amber-500 flex-shrink-0" />
                <div>
                  <span className="font-medium">{t.condition}</span>
                  <span className="text-gray-500"> → {t.action}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Watch signals */}
      {decision.watch_signals && decision.watch_signals.length > 0 && (
        <div className="mb-3">
          <p className="text-xs font-semibold text-gray-500 mb-1">Watch Signals</p>
          <div className="flex flex-wrap gap-1">
            {decision.watch_signals.map((s, i) => (
              <span
                key={i}
                className="text-xs bg-white/70 border border-gray-200 rounded px-2 py-0.5 text-gray-600"
              >
                {s}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Dissenting view */}
      {decision.dissenting_view && (
        <div className="bg-white/50 border border-gray-200 rounded p-2 text-xs text-gray-600 italic">
          <span className="font-semibold not-italic text-gray-500">Dissenting view: </span>
          {decision.dissenting_view}
        </div>
      )}
    </div>
  );
};
