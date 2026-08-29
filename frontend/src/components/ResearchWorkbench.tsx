import { useMemo, useState } from "react";
import {
    api,
    EvidenceScoreResponse,
    ResearchBriefValidateRequest,
    ResearchBriefValidateResponse,
    ResearchSearchResponse,
} from "../services/api";

type Props = {
  language: "en" | "zh";
};

const DEFAULT_ENGINES = ["google", "duckduckgo", "bing-int", "baidu"];

export function ResearchWorkbench({ language }: Props) {
  const [brief, setBrief] = useState<ResearchBriefValidateRequest>({
    decision:
      language === "en"
        ? "Set next-week C5/C9 quote strategy for East China"
        : "制定华东市场下周C5/C9报价策略",
    target_customer: language === "en" ? "Resin converters" : "树脂下游客户",
    geography: language === "en" ? "East China" : "华东",
    category_or_substitute_set:
      language === "en" ? "C5/C9 chain and substitutes" : "C5/C9产业链与替代品",
    time_horizon: language === "en" ? "1-4 weeks" : "1-4周",
    must_answer_questions:
      language === "en"
        ? [
            "How is feedstock cost changing?",
            "Are competitors moving prices?",
            "Is downstream demand weakening?",
          ]
        : ["原料成本如何变化？", "竞品是否在调价？", "下游需求是否走弱？"],
    evidence_bar: "medium",
  });

  const [query, setQuery] = useState(
    language === "en"
      ? "naphtha cracked c5 price trend East China this week"
      : "本周 华东 石脑油 裂解C5 价格 走势",
  );
  const [engines, setEngines] = useState<string>(DEFAULT_ENGINES.join(", "));
  const [maxPerEngine, setMaxPerEngine] = useState<number>(4);
  const [includeNews, setIncludeNews] = useState(true);
  const [timeLimit, setTimeLimit] = useState<"d" | "w" | "m">("w");

  const [briefResult, setBriefResult] =
    useState<ResearchBriefValidateResponse | null>(null);
  const [searchResult, setSearchResult] =
    useState<ResearchSearchResponse | null>(null);
  const [scoreResult, setScoreResult] = useState<EvidenceScoreResponse | null>(
    null,
  );
  const [isBusy, setIsBusy] = useState(false);
  const [error, setError] = useState<string>("");

  const selectedEngines = useMemo(
    () =>
      engines
        .split(",")
        .map((v) => v.trim())
        .filter(Boolean),
    [engines],
  );

  const onValidateBrief = async () => {
    setIsBusy(true);
    setError("");
    try {
      const result = await api.validateResearchBrief(brief);
      setBriefResult(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Validation failed");
    } finally {
      setIsBusy(false);
    }
  };

  const onRunSearch = async () => {
    setIsBusy(true);
    setError("");
    try {
      const result = await api.runResearchSearch({
        query,
        engines: selectedEngines.length ? selectedEngines : DEFAULT_ENGINES,
        max_results_per_engine: maxPerEngine,
        include_news: includeNews,
        timelimit: timeLimit,
      });
      setSearchResult(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Search failed");
    } finally {
      setIsBusy(false);
    }
  };

  const onScoreEvidence = async () => {
    if (!searchResult?.results?.length) {
      setError(language === "en" ? "Run search first" : "请先执行搜索");
      return;
    }
    setIsBusy(true);
    setError("");
    try {
      const score = await api.scoreResearchEvidence(
        searchResult.results.map((r) => ({
          title: r.title,
          url: r.url,
          source_type: "news",
          published_date: r.published_date,
          corroboration_count: 1,
        })),
      );
      setScoreResult(score);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Scoring failed");
    } finally {
      setIsBusy(false);
    }
  };

  return (
    <div className="h-full overflow-y-auto bg-slate-50">
      <div className="max-w-6xl mx-auto px-6 py-6 space-y-6">
        <div>
          <h2 className="text-xl font-semibold text-slate-900">
            {language === "en" ? "Research Workbench" : "研究工作台"}
          </h2>
          <p className="text-sm text-slate-600 mt-1">
            {language === "en"
              ? "Validate research briefs, run multi-engine search, and score evidence confidence."
              : "用于验证研究简报、执行多引擎检索，并计算证据置信度。"}
          </p>
        </div>

        <section className="bg-white border border-slate-200 rounded-xl p-4 space-y-3">
          <h3 className="text-sm font-semibold text-slate-800">
            {language === "en"
              ? "1) Research Brief Validation"
              : "1）研究简报校验"}
          </h3>
          <input
            className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm"
            value={brief.decision}
            onChange={(e) =>
              setBrief((b) => ({ ...b, decision: e.target.value }))
            }
            placeholder={language === "en" ? "Decision" : "决策问题"}
          />
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <input
              className="border border-slate-300 rounded-lg px-3 py-2 text-sm"
              value={brief.target_customer}
              onChange={(e) =>
                setBrief((b) => ({ ...b, target_customer: e.target.value }))
              }
              placeholder={language === "en" ? "Target customer" : "目标客户"}
            />
            <input
              className="border border-slate-300 rounded-lg px-3 py-2 text-sm"
              value={brief.geography}
              onChange={(e) =>
                setBrief((b) => ({ ...b, geography: e.target.value }))
              }
              placeholder={language === "en" ? "Geography" : "区域"}
            />
          </div>
          <button
            type="button"
            onClick={onValidateBrief}
            disabled={isBusy}
            className="px-3 py-2 rounded-lg bg-slate-900 text-white text-sm disabled:opacity-50"
          >
            {language === "en" ? "Validate Brief" : "校验简报"}
          </button>
          {briefResult && (
            <div
              className={`text-sm ${briefResult.ok ? "text-emerald-700" : "text-amber-700"}`}
            >
              {briefResult.ok
                ? language === "en"
                  ? "Brief is valid"
                  : "简报校验通过"
                : `${language === "en" ? "Issues" : "问题"}: ${briefResult.issues.join("; ")}`}
            </div>
          )}
        </section>

        <section className="bg-white border border-slate-200 rounded-xl p-4 space-y-3">
          <h3 className="text-sm font-semibold text-slate-800">
            {language === "en" ? "2) Multi-Engine Search" : "2）多引擎检索"}
          </h3>
          <input
            className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={language === "en" ? "Search query" : "搜索问题"}
          />
          <input
            className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm"
            value={engines}
            onChange={(e) => setEngines(e.target.value)}
            placeholder="google, duckduckgo, bing-int, baidu"
          />
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <label className="flex items-center gap-2">
              <span>{language === "en" ? "Max/engine" : "每引擎数量"}</span>
              <input
                type="number"
                min={1}
                max={10}
                value={maxPerEngine}
                onChange={(e) => setMaxPerEngine(Number(e.target.value) || 4)}
                className="w-20 border border-slate-300 rounded px-2 py-1"
              />
            </label>
            <label className="flex items-center gap-2">
              <span>{language === "en" ? "Time" : "时间窗"}</span>
              <select
                value={timeLimit}
                onChange={(e) =>
                  setTimeLimit(e.target.value as "d" | "w" | "m")
                }
                className="border border-slate-300 rounded px-2 py-1"
              >
                <option value="d">1d</option>
                <option value="w">1w</option>
                <option value="m">1m</option>
              </select>
            </label>
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={includeNews}
                onChange={(e) => setIncludeNews(e.target.checked)}
              />
              <span>{language === "en" ? "Include news" : "包含新闻"}</span>
            </label>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={onRunSearch}
              disabled={isBusy}
              className="px-3 py-2 rounded-lg bg-slate-900 text-white text-sm disabled:opacity-50"
            >
              {language === "en" ? "Run Search" : "执行检索"}
            </button>
            <button
              type="button"
              onClick={onScoreEvidence}
              disabled={isBusy}
              className="px-3 py-2 rounded-lg border border-slate-300 text-slate-800 text-sm disabled:opacity-50"
            >
              {language === "en" ? "Score Evidence" : "计算证据分"}
            </button>
          </div>

          {searchResult && (
            <div className="text-sm text-slate-700">
              {language === "en" ? "Results" : "结果"}: {searchResult.count}
            </div>
          )}
          {searchResult?.results?.length ? (
            <div className="max-h-64 overflow-auto border border-slate-200 rounded-lg">
              {searchResult.results.slice(0, 20).map((r, idx) => (
                <div
                  key={`${r.url}-${idx}`}
                  className="p-3 border-b border-slate-100 last:border-b-0"
                >
                  <p className="text-sm font-medium text-slate-900">
                    {r.title || "(untitled)"}
                  </p>
                  <a
                    href={r.url}
                    target="_blank"
                    rel="noreferrer"
                    className="text-xs text-blue-700 break-all"
                  >
                    {r.url}
                  </a>
                  <p className="text-xs text-slate-600 mt-1">
                    {r.source} {r.published_date ? `• ${r.published_date}` : ""}
                  </p>
                </div>
              ))}
            </div>
          ) : null}

          {scoreResult && (
            <div className="text-sm text-slate-800 border border-slate-200 rounded-lg p-3 bg-slate-50">
              <div>
                {language === "en" ? "Evidence score" : "证据评分"}:{" "}
                <strong>{scoreResult.score.score}/100</strong>
              </div>
              <div>
                {language === "en" ? "Confidence" : "置信级别"}:{" "}
                <strong>{scoreResult.score.confidence_label}</strong>
              </div>
              <div>
                {language === "en" ? "Source diversity" : "来源多样性"}:{" "}
                {scoreResult.score.source_diversity}
              </div>
              <div>
                {language === "en" ? "Domain diversity" : "域名多样性"}:{" "}
                {scoreResult.score.domain_diversity}
              </div>
              {!!scoreResult.score.notes.length && (
                <div>
                  {language === "en" ? "Notes" : "备注"}:{" "}
                  {scoreResult.score.notes.join("; ")}
                </div>
              )}
            </div>
          )}

          {!!error && <div className="text-sm text-rose-700">{error}</div>}
        </section>
      </div>
    </div>
  );
}
