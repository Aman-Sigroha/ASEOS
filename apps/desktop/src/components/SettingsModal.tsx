import React from "react";
import { Settings, X, Sparkles, Sliders, Check } from "lucide-react";

export type SettingsModalProps = {
  isOpen: boolean;
  onClose: () => void;
  selectedModel: string;
  onSelectModel: (m: string) => void;
  stepDelayMs: number;
  onSelectStepDelay: (delay: number) => void;
  simulateFailure: boolean;
  onToggleSimulateFailure: () => void;
  fontSize: number;
  onSelectFontSize: (size: number) => void;
};

const MODELS = [
  { id: "claude-3-7-sonnet", name: "Claude 3.7 Sonnet (Mock)", provider: "Anthropic" },
  { id: "gemini-2-5-flash", name: "Gemini 2.5 Flash (Mock)", provider: "Google" },
  { id: "gpt-4o", name: "GPT-4o (Mock)", provider: "OpenAI" },
];

const DELAYS = [
  { label: "Fast (350ms)", value: 350 },
  { label: "Normal (700ms)", value: 700 },
  { label: "Step-by-Step (1200ms)", value: 1200 },
];

const FONT_SIZES = [12, 13, 14, 15, 16];

export const SettingsModal: React.FC<SettingsModalProps> = ({
  isOpen,
  onClose,
  selectedModel,
  onSelectModel,
  stepDelayMs,
  onSelectStepDelay,
  simulateFailure,
  onToggleSimulateFailure,
  fontSize,
  onSelectFontSize,
}) => {
  if (!isOpen) return null;

  return (
    <div className="modal-overlay" onClick={onClose} role="dialog" aria-modal="true">
      <div className="modal-content settings-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <div className="modal-title-row">
            <Settings size={16} className="modal-title-icon" />
            <h3>ASEOS Workspace Settings</h3>
          </div>
          <button className="modal-close-btn" onClick={onClose} aria-label="Close modal">
            <X size={15} />
          </button>
        </div>

        <div className="modal-body settings-body">
          {/* Section: AI Model */}
          <div className="settings-section">
            <div className="settings-section-header">
              <Sparkles size={14} className="section-icon" />
              <span>AI Agent Model</span>
            </div>
            <div className="model-options-list">
              {MODELS.map((model) => {
                const isSelected = selectedModel === model.id;
                return (
                  <div
                    key={model.id}
                    className={`model-option-card ${isSelected ? "selected" : ""}`}
                    onClick={() => onSelectModel(model.id)}
                    role="button"
                    tabIndex={0}
                  >
                    <div className="model-info">
                      <span className="model-name">{model.name}</span>
                      <span className="model-provider">{model.provider}</span>
                    </div>
                    {isSelected && <Check size={14} className="model-check" />}
                  </div>
                );
              })}
            </div>
          </div>

          {/* Section: Simulation Speed */}
          <div className="settings-section">
            <div className="settings-section-header">
              <Sliders size={14} className="section-icon" />
              <span>Mock Simulation Speed</span>
            </div>
            <div className="delay-chips">
              {DELAYS.map((d) => (
                <button
                  key={d.value}
                  className={`delay-chip ${stepDelayMs === d.value ? "active" : ""}`}
                  onClick={() => onSelectStepDelay(d.value)}
                >
                  {d.label}
                </button>
              ))}
            </div>
          </div>

          {/* Section: Editor Font Size */}
          <div className="settings-section">
            <div className="settings-section-header">
              <span>Editor Font Size ({fontSize}px)</span>
            </div>
            <div className="font-size-options">
              {FONT_SIZES.map((size) => (
                <button
                  key={size}
                  className={`font-size-btn ${fontSize === size ? "active" : ""}`}
                  onClick={() => onSelectFontSize(size)}
                >
                  {size}px
                </button>
              ))}
            </div>
          </div>

          {/* Section: Edge Case Simulation */}
          <div className="settings-section">
            <div className="settings-section-header">
              <span>Testing & Simulation</span>
            </div>
            <label className="settings-checkbox-row">
              <input
                type="checkbox"
                checked={simulateFailure}
                onChange={onToggleSimulateFailure}
              />
              <div className="checkbox-text">
                <span className="checkbox-title">Simulate Execution Failure</span>
                <span className="checkbox-desc">
                  When enabled, the mock task sequence will trigger verification failure to test error UI states.
                </span>
              </div>
            </label>
          </div>
        </div>

        <div className="modal-footer">
          <button className="settings-done-btn" onClick={onClose}>
            Done
          </button>
        </div>
      </div>
    </div>
  );
};

export default SettingsModal;
