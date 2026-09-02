// Gmail-style email compose modal for contacting paper authors.

import { useState, useEffect } from 'react';
import { X, CheckCircle, AlertTriangle } from 'lucide-react';
import { databaseApi } from '../services/databaseApi';
import { useTranslation } from '../hooks/useTranslation';
import { translateErrorMessage } from '../utils/error';

interface EmailComposeModalProps {
  paperName: string;
  paperTitle: string;
  authors: string[];
  emails: string[];
  onClose: () => void;
  artifactId: number;
  projectId: string;
  onSent?: (artifactId: number) => void;
}

export function EmailComposeModal({ paperName, paperTitle, authors, emails, onClose, artifactId, projectId, onSent }: EmailComposeModalProps) {
  const { t } = useTranslation();
  const [emailChips, setEmailChips] = useState<string[]>(emails.filter(e => e.trim()));
  const [chipInput, setChipInput] = useState('');

  const titleToUse = paperTitle || paperName;
  const lastName = authors[0] ? authors[0].trim().split(/\s+/).pop() : '';
  const greeting = lastName ? `Hello Professor ${lastName}` : 'Hello Professor';

  const fallbackBody = `${greeting},

I am writing to request access to the data used in your publication: "${titleToUse}".

I am conducting research on cancer drug response prediction and your dataset would be invaluable for my study. Would it be possible to obtain access to the data, or could you direct me to where I might access it?

Thank you for your consideration.

Best regards`;

  const [subject, setSubject] = useState(`Data Request - ${titleToUse}`);
  const [body, setBody] = useState('');
  const [isGenerating, setIsGenerating] = useState(true);
  const [isSending, setIsSending] = useState(false);
  const [success, setSuccess] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    databaseApi.generateEmail(artifactId, projectId)
      .then((result) => {
        if (!cancelled) {
          setSubject(result.subject);
          setBody(result.body);
          setIsGenerating(false);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setBody(fallbackBody);
          setIsGenerating(false);
        }
      });

    return () => { cancelled = true; };
  }, [artifactId, projectId]);

  const addChip = (email: string) => {
    const trimmed = email.trim();
    if (trimmed && !emailChips.includes(trimmed)) {
      setEmailChips(prev => [...prev, trimmed]);
    }
    setChipInput('');
  };

  const removeChip = (index: number) => {
    setEmailChips(prev => prev.filter((_, i) => i !== index));
  };

  const handleChipInputKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' || e.key === ',' || e.key === 'Tab') {
      e.preventDefault();
      if (chipInput.trim()) {
        addChip(chipInput);
      }
    } else if (e.key === 'Backspace' && chipInput === '' && emailChips.length > 0) {
      removeChip(emailChips.length - 1);
    }
  };

  const handleChipInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const value = e.target.value;
    if (value.includes(',')) {
      const parts = value.split(',');
      parts.forEach((part, i) => {
        if (i < parts.length - 1) {
          if (part.trim()) addChip(part);
        } else {
          setChipInput(part);
        }
      });
    } else {
      setChipInput(value);
    }
  };

  const handleChipInputBlur = () => {
    if (chipInput.trim()) {
      addChip(chipInput);
    }
  };

  const handleSend = async () => {
    const emailList = emailChips.filter(e => e.trim());

    if (emailList.length === 0) {
      setError(t('email.error_no_recipients'));
      return;
    }

    setIsSending(true);
    setError(null);

    try {
      await databaseApi.sendEmail({
        to: emailList,
        subject,
        body,
        project_id: projectId,
        artifact_id: artifactId,
      });

      setSuccess(true);

      if (onSent) {
        onSent(artifactId);
      }

      setTimeout(() => {
        onClose();
      }, 2000);
    } catch (err) {
      setError(translateErrorMessage(err instanceof Error ? err.message : null, t, 'email.send_failed'));
    } finally {
      setIsSending(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
      <div
        className="bg-white rounded-lg shadow-xl w-full max-w-4xl flex flex-col"
        style={{ maxHeight: '85vh' }}
      >
        <div className="flex items-center justify-between px-4 py-3 bg-gray-800 rounded-t-lg">
          <h2 className="text-sm font-medium text-white">{t('email.new_message')}</h2>
          <button
            onClick={onClose}
            className="text-gray-300 hover:text-white transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {success && (
          <div className="mx-4 mt-3 p-3 bg-green-50 border border-green-200 rounded flex items-center space-x-2">
            <CheckCircle className="w-4 h-4 text-green-600" />
            <span className="text-sm text-green-800">{t('email.sent_success')}</span>
          </div>
        )}

        {error && (
          <div className="mx-4 mt-3 p-3 bg-red-50 border border-red-200 rounded flex items-center space-x-2">
            <AlertTriangle className="w-4 h-4 text-red-600" />
            <span className="text-sm text-red-800">{error}</span>
          </div>
        )}

        <div className="flex-1 flex flex-col overflow-y-auto">
          <div className="flex items-start border-b border-gray-200 py-2 px-4">
            <span className="text-sm text-gray-500 mt-1.5 mr-2 flex-shrink-0">{t('email.to_label')}</span>
            <div className="flex flex-wrap items-center gap-1 flex-1 min-h-[32px]">
              {emailChips.map((email, index) => (
                <span
                  key={index}
                  className="inline-flex items-center bg-gray-100 text-gray-800 text-sm rounded-full px-3 py-1 max-w-[280px]"
                >
                  <span className="truncate">{email}</span>
                  <button
                    type="button"
                    onClick={() => removeChip(index)}
                    className="ml-1.5 text-gray-500 hover:text-gray-700 flex-shrink-0"
                  >
                    <X className="w-3 h-3" />
                  </button>
                </span>
              ))}
              <input
                type="text"
                value={chipInput}
                onChange={handleChipInputChange}
                onKeyDown={handleChipInputKeyDown}
                onBlur={handleChipInputBlur}
                placeholder={emailChips.length === 0 ? t('email.recipients_placeholder') : ""}
                className="flex-1 min-w-[120px] outline-none text-sm py-1 bg-transparent"
              />
            </div>
          </div>

          <div className="flex items-center border-b border-gray-200 py-2 px-4">
            <span className="text-sm text-gray-500 mr-2 flex-shrink-0">{t('email.subject_label')}</span>
            <input
              type="text"
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              className="flex-1 outline-none text-sm bg-transparent"
            />
          </div>

          <div className="flex-1 px-4 py-3">
{isGenerating ? (
              <div className="flex items-center space-x-3 text-gray-400 py-8 justify-center">
                <div className="w-4 h-4 border-2 border-gray-300 border-t-blue-500 rounded-full animate-spin" />
                <span className="text-sm">{t('email.drafting')}</span>
              </div>
            ) : (
              <textarea
                value={body}
                onChange={(e) => setBody(e.target.value)}
                className="w-full h-full min-h-[400px] outline-none text-sm resize-none bg-transparent"
                placeholder={t('email.compose_placeholder')}
              />
            )}
          </div>
        </div>

        <div className="flex items-center justify-between px-4 py-3 border-t border-gray-200">
          <button
            onClick={handleSend}
            disabled={isSending || success || isGenerating}
            className="px-6 py-2 bg-blue-600 text-white rounded-full text-sm font-medium hover:bg-blue-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center space-x-2"
          >
            {isSending ? (
              <>
                <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
                <span>{t('email.sending')}</span>
              </>
            ) : (
              <span>{t('email.send_button')}</span>
            )}
          </button>
          <button
            onClick={onClose}
            disabled={isSending}
            className="text-gray-400 hover:text-gray-600 transition-colors"
            title={t('email.discard_title')}
          >
            <X className="w-5 h-5" />
          </button>
        </div>
      </div>
    </div>
  );
}
