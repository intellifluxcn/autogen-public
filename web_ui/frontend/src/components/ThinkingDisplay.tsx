// Displays AI agent thinking text in real-time with a pulsing indicator.

import React from 'react';
import { Brain } from 'lucide-react';
import { cn } from '../utils';

interface ThinkingDisplayProps {
  thinking: string | null;
  agentName: string | null;
  className?: string;
}

export const ThinkingDisplay: React.FC<ThinkingDisplayProps> = ({
  thinking,
  agentName,
  className
}) => {
  if (!thinking) {
    return null;
  }

  return (
    <div className={cn(
      "bg-gradient-to-r from-amber-50 to-orange-50 border border-amber-200 rounded-lg p-4 shadow-sm",
      className
    )}>
      <div className="flex items-start space-x-3">
        <div className="flex-shrink-0 mt-0.5">
          <Brain className="w-5 h-5 text-amber-600 animate-pulse" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center space-x-2 mb-1">
            <h3 className="text-sm font-semibold text-amber-900">
              AI Thinking
            </h3>
            {agentName && (
              <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-amber-100 text-amber-800">
                {agentName}
              </span>
            )}
          </div>
          <p className="text-sm text-amber-800 leading-relaxed break-words">
            {thinking}
          </p>
        </div>
      </div>
    </div>
  );
};
