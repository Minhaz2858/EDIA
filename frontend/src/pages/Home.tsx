import { useState, useRef, useEffect } from "react";
import { ChatInput } from "@/components/ChatInput";
import { ChatMessage, Message } from "@/components/ChatMessage";
import { api, UserPrefs } from "@/services/api";
import { loadUserPrefs, saveUserPrefs } from "@/lib/userPrefs";

export default function Home() {
  const [messages, setMessages] = useState<Message[]>([
    {
      role: "assistant",
      content: "Hello! I am your Enterprise Decision Intelligence Agent (EDIA). How can I help you today? You can ask me about market analysis, sales forecasts, or pricing strategies.",
    },
  ]);
  const [loading, setLoading] = useState(false);
  const [prefs, setPrefs] = useState<UserPrefs>(() => loadUserPrefs());
  const [preferredProductsText, setPreferredProductsText] = useState<string>(
    (loadUserPrefs().preferred_products || []).join(", ")
  );
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  useEffect(() => {
    saveUserPrefs(prefs);
  }, [prefs]);

  const handleSendMessage = async (content: string) => {
    // Add user message
    const userMessage: Message = { role: "user", content };
    setMessages((prev) => [...prev, userMessage]);
    setLoading(true);

    try {
      // Call API
      const response = await api.sendMessage(content, undefined, prefs, []);

      // Add bot message
      const botMessage: Message = {
        role: "assistant",
        content: response.markdown,
        context: response.context
      };

      setMessages((prev) => [...prev, botMessage]);
    } catch (error) {
      console.error(error);
      const errorMessage: Message = {
        role: "assistant",
        content: "Sorry, I encountered an error while processing your request. Please check if the backend server is running."
      };
      setMessages((prev) => [...prev, errorMessage]);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex flex-col h-screen bg-gray-50">
      {/* Header */}
      <header className="bg-white border-b border-gray-200 p-4 shadow-sm">
        <div className="max-w-4xl mx-auto flex items-center justify-between">
          <h1 className="text-xl font-bold text-gray-800 flex items-center gap-2">
            📊 Enterprise Decision Intelligence Agent
          </h1>
          <div className="flex items-center gap-2">
            <div className="flex items-center gap-2 text-xs">
              <select
                className="border border-gray-200 rounded px-2 py-1 bg-white"
                value={prefs.language || "zh"}
                onChange={(e) =>
                  setPrefs((p) => ({ ...p, language: e.target.value as "zh" | "en" }))
                }
                aria-label="Language preference"
              >
                <option value="zh">中文</option>
                <option value="en">English</option>
              </select>

              <select
                className="border border-gray-200 rounded px-2 py-1 bg-white"
                value={prefs.delta_basis || "wow"}
                onChange={(e) =>
                  setPrefs((p) => ({ ...p, delta_basis: e.target.value as "wow" | "mom" }))
                }
                aria-label="Delta basis preference"
              >
                <option value="wow">WoW(7d)</option>
                <option value="mom">MoM(30d)</option>
              </select>

              <select
                className="border border-gray-200 rounded px-2 py-1 bg-white"
                value={prefs.default_format || "full_report"}
                onChange={(e) =>
                  setPrefs((p) => ({
                    ...p,
                    default_format: e.target.value as "full_report" | "structured" | "concise",
                  }))
                }
                aria-label="Default format preference"
              >
                <option value="full_report">01–05</option>
                <option value="structured">Short</option>
              </select>

              <input
                className="border border-gray-200 rounded px-2 py-1 bg-white w-44"
                value={preferredProductsText}
                onChange={(e) => setPreferredProductsText(e.target.value)}
                onBlur={() => {
                  const list = preferredProductsText
                    .split(/[,，]/)
                    .map((s) => s.trim())
                    .filter(Boolean);
                  setPrefs((p) => ({ ...p, preferred_products: list }));
                }}
                placeholder="Pinned products"
                aria-label="Preferred products"
              />
            </div>

            <span className="text-xs px-2 py-1 bg-blue-100 text-blue-700 rounded-full font-medium">
              V1.0 Beta
            </span>
          </div>
        </div>
      </header>

      {/* Chat Area */}
      <main className="flex-1 overflow-y-auto p-4 space-y-6">
        {messages.map((msg, index) => (
          <ChatMessage key={index} message={msg} />
        ))}
        {loading && (
          <div className="flex justify-start max-w-4xl mx-auto px-4">
            <div className="bg-white p-4 rounded-lg shadow-sm border border-gray-100 flex items-center gap-2">
              <div className="w-2 h-2 bg-blue-500 rounded-full animate-bounce" />
              <div className="w-2 h-2 bg-blue-500 rounded-full animate-bounce delay-75" />
              <div className="w-2 h-2 bg-blue-500 rounded-full animate-bounce delay-150" />
              <span className="text-sm text-gray-500 ml-2">Analyzing market data...</span>
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </main>

      {/* Input Area */}
      <footer className="bg-white border-t border-gray-200">
        <ChatInput onSend={handleSendMessage} disabled={loading} />
      </footer>
    </div>
  );
}
