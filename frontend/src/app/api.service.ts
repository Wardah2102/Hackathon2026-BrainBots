import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../environments/environment';

export type QueryStatus = 'sql' | 'clarify' | 'refused' | 'unavailable' | 'no_data' | 'error' | 'timeout';
export interface QueryResult { question: string; sql: string; dialect: string; status: QueryStatus; explanation: string; message: string; tables_used: string[]; display_columns: string[]; criteria_applied: string[]; validated: boolean; validation_error: string | null; sample_rows: Array<Record<string, unknown>>; row_count: number | null; truncated: boolean; error: string | null; }
export interface ConversationSummary { session_id: string; created_at: string | null; updated_at: string | null; turn_count: number; last_question: string | null; }
export interface ConversationTurn { timestamp: string; question: string; result: QueryResult; }
export interface ConversationRecord { session_id: string; created_at: string | null; updated_at: string | null; turns: ConversationTurn[]; }
export interface ConversationResponse { session_id: string; result: QueryResult; }
export type AuthMethod = 'windows' | 'sql';
export interface ConnectRequest { server: string; database: string; auth: AuthMethod; username?: string | null; password?: string | null; }
export interface ConnectResponse { database_id: string; cached: boolean; dialect: string; table_count: number; }
export interface TestConnectionResponse { ok: boolean; dialect: string; }
export interface AnalysisResponse { database_id: string; dialect: string; database: string | null; table_count: number; column_count: number; relationship_count: number; }

@Injectable({ providedIn: 'root' })
export class ApiService {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = environment.apiUrl;
  health(): Observable<{ status: string }> { return this.http.get<{ status: string }>(`${this.baseUrl}/health`); }
  domains(): Observable<{ domains: string[] }> { return this.http.get<{ domains: string[] }>(`${this.baseUrl}/domains`); }
  conversations(): Observable<ConversationSummary[]> { return this.http.get<ConversationSummary[]>(`${this.baseUrl}/conversations`); }
  conversation(id: string): Observable<ConversationRecord> { return this.http.get<ConversationRecord>(`${this.baseUrl}/conversations/${id}`); }
  connect(req: ConnectRequest): Observable<ConnectResponse> { return this.http.post<ConnectResponse>(`${this.baseUrl}/connect`, req); }
  testConnection(req: ConnectRequest): Observable<TestConnectionResponse> { return this.http.post<TestConnectionResponse>(`${this.baseUrl}/connect/test`, req); }
  analyzeDatabase(databaseId: string): Observable<AnalysisResponse> { return this.http.get<AnalysisResponse>(`${this.baseUrl}/databases/${databaseId}/analysis`); }  converse(question: string, domain: string | null, sessionId: string | null, databaseId: string | null = null): Observable<ConversationResponse> {
    return this.http.post<ConversationResponse>(`${this.baseUrl}/converse`, { question, domain, session_id: sessionId, database_id: databaseId });
  }
  endConversation(id: string): Observable<{ status: string; session_id: string }> { return this.http.delete<{ status: string; session_id: string }>(`${this.baseUrl}/converse/${id}`); }
}
