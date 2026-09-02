// Socket.IO client with event routing, authentication, and reconnection support.

import { io, Socket } from 'socket.io-client';

const SOCKET_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

class SocketClient {
  private socket: Socket | null = null;
  private listeners: Map<string, Set<(data: any) => void>> = new Map();
  private wiredInstance: Socket | null = null;

  /** Remove engine listeners so we can safely re-bind after replacing the socket. */
  private resetEngineWiring(): void {
    if (!this.socket) return;
    this.socket.io.off('reconnect_attempt');
    this.socket.io.off('reconnect');
    this.socket.io.off('reconnect_error');
    this.wiredInstance = null;
  }

  private wireEngine(thisSocket: Socket): void {
    if (this.wiredInstance === thisSocket) return;
    this.resetEngineWiring();
    this.wiredInstance = thisSocket;

    thisSocket.on('connect', () => {
      console.log('Connected to WebSocket server');
    });

    // Server-initiated disconnect (e.g. lifespan shutdown, auth invalidated)
    // should NOT auto-reconnect — repeated reconnect attempts with a stale or
    // expired token cause an infinite 401 loop on the backend.
    thisSocket.on('disconnect', (reason: string) => {
      console.log('Disconnected from WebSocket server:', reason);
      // No manual reconnect on `io server disconnect`; let the user log back
      // in (or the browser refresh) re-establish the session.
    });

    thisSocket.on('connect_error', (error: Error) => {
      console.error('WebSocket connection error:', error);
      // Treat 401-style auth failures as "session invalid" — clear the token
      // and force the user back through login. socket.io surfaces this
      // either via an Error.message containing "auth"/"401"/"unauthorized"
      // or an Error.data.code from the server's connect handler.
      const msg = (error?.message || '').toLowerCase();
      const looksUnauthorized =
        msg.includes('unauthorized') ||
        msg.includes('auth') ||
        msg.includes('401') ||
        msg.includes('forbidden');
      if (looksUnauthorized) {
        sessionStorage.removeItem('auth_token');
        // Stop further reconnect attempts.
        thisSocket.disconnect();
        if (typeof window !== 'undefined' && window.location.pathname !== '/login') {
          window.location.assign('/login');
        }
      }
    });

    thisSocket.io.on('reconnect_attempt', () => {
      const token = sessionStorage.getItem('auth_token');
      if (token && this.socket) {
        this.socket.auth = { token };
      } else if (this.socket) {
        // No token any more (logged out) — stop reconnecting.
        this.socket.disconnect();
      }
    });

    thisSocket.io.on('reconnect_error', (err: Error) => {
      console.warn('WebSocket reconnect error:', err);
    });

    thisSocket.io.on('reconnect_failed', () => {
      console.warn('WebSocket reconnect attempts exhausted; giving up.');
    });
  }

  private teardownSocket(): void {
    if (this.socket) {
      this.resetEngineWiring();
      this.socket.removeAllListeners();
      this.socket.disconnect();
      this.socket = null;
    }
    this.wiredInstance = null;
  }

  connect(): Socket {
    const token = sessionStorage.getItem('auth_token');

    if (!token) {
      this.teardownSocket();
      console.warn('Cannot connect to Socket.IO: No auth token available');
      return io(SOCKET_URL, { autoConnect: false });
    }

    if (this.socket) {
      this.socket.auth = { token };
      this.wireEngine(this.socket);
      this.setupMessageRouting();
      return this.socket;
    }

    this.socket = io(SOCKET_URL, {
      transports: ['websocket', 'polling'],
      autoConnect: true,
      timeout: 20000,
      reconnection: true,
      // Bounded retries — an unbounded loop with an expired token spammed
      // the backend with 401s and burned CPU.
      reconnectionAttempts: 5,
      reconnectionDelay: 1000,
      reconnectionDelayMax: 30000,
      auth: { token },
    });

    this.wireEngine(this.socket);
    this.setupMessageRouting();
    return this.socket;
  }

  disconnect(): void {
    this.teardownSocket();
    this.listeners.clear();
  }

  reconnect(): void {
    this.disconnect();
    this.connect();
  }

  private setupMessageRouting(): void {
    if (!this.socket) return;

    const eventTypes = [
      'project_created',
      'project_updated',
      'project_deleted',
      'project_status_changed',
      'stage_updated',
      'progress_updated',
      'message_added',
      'input_requested',
      'input_response',
      'thinking_updated',
      'status_updated',
    ];

    eventTypes.forEach((eventType) => {
      this.socket!.off(eventType);
      this.socket!.on(eventType, (data) => {
        this.notifyListeners(eventType, data);
      });
    });
  }

  private notifyListeners(eventType: string, data: any): void {
    const bucket = this.listeners.get(eventType);
    if (bucket) {
      bucket.forEach((listener) => {
        try {
          listener(data);
        } catch (error) {
          console.error(`Error in ${eventType} listener:`, error);
        }
      });
    }
  }

  on(eventType: string, callback: (data: any) => void): () => void {
    if (!this.listeners.has(eventType)) {
      this.listeners.set(eventType, new Set());
    }

    this.listeners.get(eventType)!.add(callback);

    return () => {
      const listeners = this.listeners.get(eventType);
      if (listeners) {
        listeners.delete(callback);
        if (listeners.size === 0) {
          this.listeners.delete(eventType);
        }
      }
    };
  }

  joinProject(projectId: string): void {
    if (this.socket?.connected) {
      this.socket.emit('join_project', { project_id: projectId });
    }
  }

  emit(eventType: string, data: any): void {
    if (this.socket?.connected) {
      this.socket.emit(eventType, data);
    }
  }

  isConnected(): boolean {
    return this.socket?.connected ?? false;
  }
}

export const socketClient = new SocketClient();
