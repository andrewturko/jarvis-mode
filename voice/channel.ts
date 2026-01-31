/**
 * UniFi Voice Channel Plugin
 *
 * Registers a channel for UniFi camera voice commands via "Hey Jarvis" wake word.
 * Routes to voice agent (Sonnet 4) for faster responses.
 * Maintains per-room session context for follow-up commands.
 */

import type { PluginApi } from "openclaw";

export const id = "unifi-voice";
export const name = "UniFi Voice Channel";

// Track sessions per room for context persistence
const roomSessions = new Map<string, { lastActivity: number }>();

// Configuration (edit these values to customize behavior)
const CONFIG = {
  immediateAck: true,      // Provide immediate TTS acknowledgments
  ackStyle: "casual"       // Response style: "casual", "formal", or "minimal"
};

export default function register(api: PluginApi) {
  const log = {
    info: (msg: string, data?: any) => api.logger.info(msg, data),
    debug: (msg: string, data?: any) => api.logger.debug(msg, data),
    warn: (msg: string, data?: any) => api.logger.warn(msg, data),
    error: (msg: string, data?: any) => api.logger.error(msg, data),
  };

  // 1. Register channel
  api.registerChannel({
    plugin: {
      id: "unifi-voice",
      meta: {
        id: "unifi-voice",
        label: "UniFi Voice",
        selectionLabel: "UniFi Camera Voice Commands",
        blurb: "Voice commands via UniFi camera microphones with Hey Jarvis wake word",
        aliases: ["jarvis-voice", "unifi"],
      },
      capabilities: {
        chatTypes: ["direct"],
        media: { send: { audio: false }, receive: { audio: true } },
      },
      config: {
        listAccountIds: () => ["default"],
        resolveAccount: (cfg: any, accountId?: string) => ({
          accountId: accountId ?? "default",
          enabled: cfg.channels?.["unifi-voice"]?.enabled !== false,
        }),
      },
      outbound: {
        deliveryMode: "direct",
        sendText: async ({ text, envelope }) => {
          // Response handled via HTTP response to Python service
          // Python service will do TTS via Sonos
          log.debug("unifi-voice outbound", { text: text.slice(0, 100) });
          return { ok: true };
        },
      },
    },
  });

  // 2. Register HTTP endpoint for voice commands (synchronous response)
  api.registerHttpRoute({
    path: "/unifi-voice/ingest",
    handler: async (req, res) => {
      try {
        // Parse request body
        let body: any = {};
        if (req.method === 'POST') {
          const chunks: Buffer[] = [];
          for await (const chunk of req) {
            chunks.push(chunk as Buffer);
          }
          const bodyStr = Buffer.concat(chunks).toString();
          try {
            body = JSON.parse(bodyStr);
          } catch (e) {
            res.writeHead(400, { "Content-Type": "application/json" });
            res.end(JSON.stringify({ error: "Invalid JSON" }));
            return;
          }
        }

        const { room, text } = body;

        if (!room || !text) {
          res.writeHead(400, { "Content-Type": "application/json" });
          res.end(JSON.stringify({ error: "Missing room or text" }));
          return;
        }

        // Session key for per-room context persistence
        const sessionId = `unifi-voice-${room}`;
        const sessionKey = `agent:voice:unifi-voice:dm:${sessionId}`;

        log.info("unifi-voice ingest", { room, text: text.slice(0, 50), sessionKey });

        // Track room activity
        roomSessions.set(room, { lastActivity: Date.now() });

        // Format as chat message for agent
        const messageTemplate = `[Jarvis Voice] ${room}: "${text}"

This is a VOICE command from ${room}. The user spoke this aloud.

Respond naturally and briefly — your response will be spoken aloud via TTS.

Keep it conversational. One or two sentences max. No markdown, no lists, no formatting.

Examples:
- "Done, I've dimmed the lights to 30%."
- "It's 72 degrees and partly cloudy."
- "Playing jazz in the kitchen."

If you can't help, say so briefly.`;

        // ASYNC APPROACH: Trigger agent processing, return immediate TTS response
        let responseText = "";

        try {
          // Send to jarvis-voice hook (agent processes in background)
          const hookUrl = `http://127.0.0.1:18789/hooks/jarvis/voice`;
          const hookPayload = JSON.stringify({ room, text });

          log.info("Triggering voice agent", { room, text: text.slice(0, 50) });

          // Fire and forget - don't wait for completion
          fetch(hookUrl, {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'Authorization': 'Bearer jarvis-motion-2026',
            },
            body: hookPayload,
          }).catch(err => {
            log.error("Hook call failed", { error: err });
          });

          // Return immediate acknowledgment for TTS (if enabled)
          if (CONFIG.immediateAck) {
            const lowerText = text.toLowerCase();

            // Define response styles
            const responses = {
              casual: {
                turnOnOff: "Got it.",
                setChange: "Done.",
                other: "On it.",
                error: "Sure."
              },
              formal: {
                turnOnOff: "Acknowledged.",
                setChange: "Complete.",
                other: "Processing.",
                error: "Understood."
              },
              minimal: {
                turnOnOff: "OK.",
                setChange: "OK.",
                other: "OK.",
                error: "OK."
              }
            };

            const style = responses[CONFIG.ackStyle] || responses.casual;

            if (lowerText.includes('turn on') || lowerText.includes('turn off')) {
              responseText = style.turnOnOff;
            } else if (lowerText.includes('set') || lowerText.includes('change')) {
              responseText = style.setChange;
            } else if (lowerText.includes('what') || lowerText.includes('how') || lowerText.includes('when')) {
              // For questions, agent might want to respond - skip TTS
              responseText = "";
            } else {
              responseText = style.other;
            }
          }

          log.info("Returning immediate response", { response: responseText });
        } catch (error) {
          console.error("[UniFi Voice] Hook trigger error:", error);
          const responses = {
            casual: "Sure.",
            formal: "Understood.",
            minimal: "OK."
          };
          responseText = responses[CONFIG.ackStyle] || "Sure.";
        }

        // Return response for TTS
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({
          response: responseText,
          room,
        }));
      } catch (error) {
        console.error("UniFi Voice ingest error:", error);
        log.error("unifi-voice ingest error", {
          error: error instanceof Error ? { message: error.message, stack: error.stack } : error
        });
        res.writeHead(500, { "Content-Type": "application/json" });
        res.end(JSON.stringify({
          error: "Internal server error",
          response: "Sorry, I encountered an error."
        }));
      }
    },
  });

  log.info("UniFi Voice channel registered with async TTS responses");
}
