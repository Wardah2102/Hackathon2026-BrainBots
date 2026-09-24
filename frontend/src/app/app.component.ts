import { Component, ViewChild, ChangeDetectorRef, ElementRef, HostListener, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClientModule } from '@angular/common/http';
import { IgxButtonModule, IgxIconModule, IgxInputGroupModule, IgxRippleModule, IgxToastModule, IgxToastComponent, IgxExcelExporterService, IgxExcelExporterOptions } from 'igniteui-angular';
import { ApiService, ConversationRecord, ConversationSummary, QueryResult, QueryStatus, AnalysisResponse } from './api.service';
import { IgSidebarComponent, IgSidebarItem, IgSidebarItemEvent } from '@sdworx/ng-ignite/sidebar';
import { IgHeaderComponent } from '@sdworx/ng-ignite/header';
import { IgDropdownComponent, IgDropdownItem } from '@sdworx/ng-ignite/dropdown';
import { IgAvatarComponent } from '@sdworx/ng-ignite/avatar';
import { IgCardComponent } from '@sdworx/ng-ignite/card';
import { IgButtonDirective } from '@sdworx/ng-ignite/button';
import { IgProgressBarComponent } from '@sdworx/ng-ignite/progress-bar';
import { igniteTheme } from '@sdworx/ng-ignite/ag-grid';
import { AgGridAngular } from 'ag-grid-angular';
import type { ColDef } from 'ag-grid-community';

interface ChatMessage { role: 'user' | 'assistant'; text: string; result?: QueryResult; }
interface AppUser { id: string; name: string; role: 'Consultant' | 'Admin'; }
interface ActiveDatabase { projectName: string; databaseId: string; dialect: string; tableCount: number; type: string; identity: string; connectedAt: string; connectedBy: string; server: string; }
interface SavedQuery { id: string; name: string; question: string; savedAt: string; result: QueryResult; }
interface QueryHelpContent {
  title: string; leadTitle: string; lead: string;
  guidanceHead: string; guidance: { title: string; desc: string }[];
  examplesHead: string; examples: { text: string; tag: string }[];
  broadHead: string; broad: { text: string; desc: string }[];
}
interface AccessUser { name: string; passId: string; email: string; role: string; status: 'Active' | 'Pending' | 'Access removed'; dateAdded: string; }

@Component({
  selector: 'app-root', standalone: true,
  imports: [IgHeaderComponent, IgSidebarComponent, IgDropdownComponent, IgAvatarComponent, IgCardComponent, IgButtonDirective, IgProgressBarComponent, AgGridAngular, CommonModule, FormsModule, HttpClientModule, IgxButtonModule, IgxIconModule, IgxInputGroupModule, IgxRippleModule, IgxToastModule],
  providers: [IgxExcelExporterService],
  templateUrl: './app.component.html', styleUrls: ['./app.component.scss']
})
export class AppComponent implements OnInit {
  private readonly api = inject(ApiService);
  private readonly excelExporter = inject(IgxExcelExporterService);
  private readonly cdr = inject(ChangeDetectorRef);
  private readonly elementRef = inject(ElementRef);
  question = ''; submittedQuestion: string | null = null; clarifyAnswer = ''; domain = 'warrants'; domains = ['warrants']; conversations: ConversationSummary[] = []; messages: ChatMessage[] = [];
  sessionId: string | null = null; isLoading = false; processing = false; apiOnline = false; activeView: 'dashboard' | 'workspace' | 'saved' | 'savedDetail' | 'settings' = 'dashboard';
  skeletonRows = [1, 2, 3, 4, 5, 6];
  users: AppUser[] = [
    { id: 'consultant', name: 'Alex Morgan', role: 'Consultant' },
    { id: 'admin', name: 'Jamie Carter', role: 'Admin' },
  ];
  currentUser: AppUser = this.users[0];
  accessSearch = '';
  accessUsers: AccessUser[] = [
    { name: 'Alex Morgan', passId: 'AM-10482', email: 'alex.morgan@sdworx.com', role: 'Consultant', status: 'Active', dateAdded: '12 Sep 2026' },
    { name: 'Jamie Carter', passId: 'JC-20573', email: 'jamie.carter@sdworx.com', role: 'Admin', status: 'Active', dateAdded: '10 Sep 2026' },
  ];
  get filteredAccessUsers(): AccessUser[] {
    const q = this.accessSearch.trim().toLowerCase();
    if (!q) return this.accessUsers;
    return this.accessUsers.filter(u =>
      u.name.toLowerCase().includes(q) || u.email.toLowerCase().includes(q) ||
      u.passId.toLowerCase().includes(q) || u.role.toLowerCase().includes(q));
  }
  initials(name: string): string { return name.split(/\s+/).filter(Boolean).map(w => w[0]).slice(0, 2).join('').toUpperCase(); }
  accessStatusClass(status: string): string { return status === 'Active' ? 'active' : status === 'Pending' ? 'pending' : 'removed'; }
  showUserDialog = false;
  userDialogMode: 'add' | 'edit' = 'add';
  userForm: { name: string; email: string; passId: string; role: 'Consultant' | 'Admin' } = { name: '', email: '', passId: '', role: 'Consultant' };
  private editingUser: AccessUser | null = null;
  private readonly accessKey = 'brainbots.accessUsers';
  showDeleteUserDialog = false; private pendingDeleteUser: AccessUser | null = null;
  private loadAccessUsers(): void {
    try {
      const raw = localStorage.getItem(this.accessKey);
      if (!raw) return;
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed) && parsed.length) this.accessUsers = parsed;
    } catch { /* keep defaults */ }
  }
  private persistAccessUsers(): void { localStorage.setItem(this.accessKey, JSON.stringify(this.accessUsers)); }
  openAddUser(): void {
    this.userDialogMode = 'add';
    this.editingUser = null;
    this.userForm = { name: '', email: '', passId: '', role: 'Consultant' };
    this.showUserDialog = true;
    this.cdr.detectChanges();
  }
  openEditUser(user: AccessUser): void {
    this.userDialogMode = 'edit';
    this.editingUser = user;
    this.userForm = { name: user.name, email: user.email, passId: user.passId, role: user.role === 'Admin' ? 'Admin' : 'Consultant' };
    this.showUserDialog = true;
    this.cdr.detectChanges();
  }
  dismissUserDialog(): void { this.showUserDialog = false; this.editingUser = null; this.cdr.detectChanges(); }
  saveUser(): void {
    const name = this.userForm.name.trim();
    const email = this.userForm.email.trim();
    const passId = this.userForm.passId.trim();
    if (!name || !email) return;
    if (this.userDialogMode === 'edit' && this.editingUser) {
      this.editingUser.name = name;
      this.editingUser.email = email;
      this.editingUser.passId = passId;
      this.editingUser.role = this.userForm.role;
      this.toast?.open(`${name} was updated.`);
    } else {
      this.accessUsers = [{ name, email, passId, role: this.userForm.role, status: 'Active', dateAdded: this.formatToday() }, ...this.accessUsers];
      this.toast?.open(`${name} was granted ${this.userForm.role} access to WarrantsTool.`);
    }
    this.persistAccessUsers();
    this.showUserDialog = false;
    this.editingUser = null;
    this.cdr.detectChanges();
  }
  deleteUser(event: Event, user: AccessUser): void {
    event.stopPropagation();
    this.pendingDeleteUser = user;
    this.showDeleteUserDialog = true;
    this.cdr.detectChanges();
  }
  dismissDeleteUserDialog(): void { this.showDeleteUserDialog = false; this.pendingDeleteUser = null; this.cdr.detectChanges(); }
  confirmDeleteUser(): void {
    const user = this.pendingDeleteUser;
    this.showDeleteUserDialog = false; this.pendingDeleteUser = null;
    if (!user) { this.cdr.detectChanges(); return; }
    this.accessUsers = this.accessUsers.filter(u => u !== user);
    this.persistAccessUsers();
    this.toast?.open(`${user.name} was removed.`);
    this.cdr.detectChanges();
  }
  private formatToday(): string { return new Date().toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' }); }
  userMenuOpen = false;
  dashboardLoading = false; dashboardError = false; queriesRun = 0;
  private readonly exportKey = 'brainbots.exportCount';
  recentExports = 0;
  savedQueries: SavedQuery[] = [];
  openedSaved: SavedQuery | null = null;
  savedError = false;
  private readonly savedKey = 'brainbots.savedQueries';
  showSaveDialog = false; saveName = ''; justSaved = false;
    currentResultSaved = false; saveNameError: string | null = null;
  showLeaveDialog = false; private pendingNewAfterSave = false; private pendingNavId: string | null = null;
  showQueryHelp = false;
  showDisconnectDialog = false;
  showUnrelatedDialog = false; pendingRefineText: string | null = null; pendingWasClarification = false;
  showDeleteQueryDialog = false; private pendingDeleteQuery: SavedQuery | null = null;
  validationNotice: string | null = null;
  consultantName = 'Alex Morgan';
  languageItems: IgDropdownItem[] = [
    { id: 'en', label: 'EN' },
    { id: 'fr', label: 'FR' },
    { id: 'nl', label: 'NL' },
  ];
  selectedLanguage: IgDropdownItem | null = this.languageItems[0];
  settingsTab: 'connection' | 'analysis' | 'access' | 'codebase' = 'connection';
  connectForm = { server: '', database: '', username: '', password: '' };
  databaseTypeItems: IgDropdownItem[] = [
    { id: 'mssql', label: 'SQL Server' },
    { id: 'postgres', label: 'PostgreSQL', disabled: true },
    { id: 'mysql', label: 'MySQL', disabled: true },
    { id: 'oracle', label: 'Oracle', disabled: true },
    { id: 'sqlite', label: 'SQLite', disabled: true },
  ];
  selectedDatabaseType: IgDropdownItem | null = this.databaseTypeItems[0];
  authItems: IgDropdownItem[] = [
    { id: 'windows', label: 'Windows authentication' },
    { id: 'sql', label: 'SQL Server authentication' },
  ];
  selectedAuth: IgDropdownItem | null = this.authItems[0];
  isTestingConnection = false;
  connectionTestPassed = false;
  connectionError: string | null = null;
  isConnectingDatabase = false;
  activeDatabase: ActiveDatabase | null = null;
  isEditingConnection = false;
  analysisStatus: 'not-started' | 'in-progress' | 'ready' = 'not-started';
  analysisStep = 0;
  analysisResult: { tables: number; columns: number; relationships: number } | null = null;
  analysisCompletedAt: string | null = null;
  analysisError: string | null = null;
  private analysisTimers: ReturnType<typeof setTimeout>[] = [];
  private readonly activeDbKey = 'brainbots.activeDatabase';
  lastResult: QueryResult | null = null;
  lastError: string | null = null;
  igniteTheme = igniteTheme;
  columnDefs: ColDef[] = [];
  rowData: Array<Record<string, unknown>> = [];
  defaultColDef: ColDef = { flex: 1, minWidth: 120, resizable: true, sortable: false, filter: false };
  @ViewChild('toast') toast?: IgxToastComponent;
  get sidebarItems(): IgSidebarItem[] {
    return [
      { id: 'dashboard', label: this.t('dashboard'), icon: 'home' },
      { id: 'workspace', label: this.t('newQuery'), icon: 'new-message' },
      { id: 'saved', label: this.t('savedQueries'), icon: 'bookmark' },
      { id: 'settings', label: this.t('settings'), icon: 'settings' },
    ];
  }
  get userQuestions(): string[] { return this.messages.filter(m => m.role === 'user').map(m => m.text); }
  get lang(): 'en' | 'fr' | 'nl' { return (this.selectedLanguage?.id as 'en' | 'fr' | 'nl') || 'en'; }
  private readonly langNames: Record<string, string> = { en: 'English', fr: 'French', nl: 'Dutch' };
  t(key: string): string { return this.i18n[this.lang]?.[key] ?? this.i18n['en'][key] ?? key; }
  private readonly i18n: Record<string, Record<string, string>> = {
    en: {
      dashboard: 'Dashboard', newQuery: 'New query', savedQueries: 'Saved queries', settings: 'Settings',
      dashboardSub: 'Overview of WarrantsTool activity and database readiness.',
      dashboardSubNoDb: 'Database availability and query access.',
      noDatabase: 'No database is available', noDatabaseSub: 'An administrator needs to connect and analyze a database before you can run queries.', refreshStatus: 'Refresh status',
      connectToStart: 'Connect your database to get started', connectToStartSub: 'Connect a trusted business database, analyze its structure, and then start asking questions in natural language.', connectDatabaseBtn: 'Connect database',
      step1Title: 'Connect database', step1Sub: 'Link a trusted business database so the agent can read its structure.',
      step2Title: 'Analyze database', step2Sub: 'Review tables, fields, and relationships before you start querying.',
      step3Title: 'Start querying', step3Sub: 'Ask questions in natural language once your database is ready.',
      queriesRun: 'Queries run', queriesSaved: 'Queries saved', recentExports: 'Recent exports', recentActivity: 'Recent activity',
      activeDb: 'Active database', askQuestion: 'Ask a specific question', conversation: 'Conversation',
      askQuestionSub: 'Use natural language to describe what you want to see.', howToWrite: 'How to write a good query',
      placeholderFirst: 'e.g. Show all active warrants for clients in Belgium', placeholderRefine: 'Describe how you want to refine these results.',
      runQuery: 'Run query', refineResults: 'Refine results', adjustQuery: 'Adjust query', startNewQuery: 'Start new query',
      refineCurrent: 'Refine current result', refineHelp: 'Use this field to refine the current results. Start a new query for a different question.',
      results: 'Results', save: 'Save', saveQuery: 'Save query', saved: 'Saved', exportExcel: 'Export to Excel', excel: 'Excel', viewSql: 'View SQL',
      resultsHere: 'Results will appear here', resultsHereSub: 'Run a query to view matching records and totals.',
      noResults: 'No results found', noResultsSub: 'The query ran successfully, but no records matched these conditions.',
      noMatching: 'No matching records', queryCompleted: 'The query completed successfully.', querySaved: 'Query saved.',
      checkingQuestion: 'Checking your question…', validating: 'Validating entities, dates, and query intent.',
      validatingSub: "This usually takes a few seconds. We'll show results as soon as the query is qualified.",
      thanksClear: 'Thanks. Your question is clear. Fetching the results…',
      creatingQuery: 'Creating query', runningQuery: 'Running query', preparingResults: 'Preparing results',
      awaiting: 'Awaiting results', fetching: 'Fetching results…', fetchingSub: 'Building the result set and preparing the table.',
      yourAnswer: 'Your answer', continue: 'Continue', cancelQuery: 'Cancel query',
      noSaved: 'No saved queries yet', noSavedSub: 'Save useful questions to return to their results without running them again.',
      you: 'You', assistant: 'Assistant', tryAgain: 'Try again', backToSaved: 'Back to saved queries',
    },
    fr: {
      dashboard: 'Tableau de bord', newQuery: 'Nouvelle requête', savedQueries: 'Requêtes enregistrées', settings: 'Paramètres',
      dashboardSub: "Aperçu de l'activité de WarrantsTool et de l'état de la base de données.",
      dashboardSubNoDb: 'Disponibilité de la base de données et accès aux requêtes.',
      noDatabase: 'Aucune base de données disponible', noDatabaseSub: 'Un administrateur doit connecter et analyser une base de données avant que vous puissiez exécuter des requêtes.', refreshStatus: 'Actualiser le statut',
      connectToStart: 'Connectez votre base de données pour commencer', connectToStartSub: 'Connectez une base de données d’entreprise de confiance, analysez sa structure, puis commencez à poser des questions en langage naturel.', connectDatabaseBtn: 'Connecter la base de données',
      step1Title: 'Connecter la base de données', step1Sub: 'Reliez une base de données d’entreprise de confiance pour que l’agent puisse lire sa structure.',
      step2Title: 'Analyser la base de données', step2Sub: 'Passez en revue les tables, champs et relations avant de commencer à interroger.',
      step3Title: 'Commencer à interroger', step3Sub: 'Posez des questions en langage naturel une fois votre base de données prête.',
      queriesRun: 'Requêtes exécutées', queriesSaved: 'Requêtes enregistrées', recentExports: 'Exports récents', recentActivity: 'Activité récente',
      activeDb: 'Base de données active', askQuestion: 'Posez une question précise', conversation: 'Conversation',
      askQuestionSub: 'Utilisez un langage naturel pour décrire ce que vous voulez voir.', howToWrite: 'Comment rédiger une bonne requête',
      placeholderFirst: 'ex. Afficher tous les warrants actifs pour les clients en Belgique', placeholderRefine: 'Décrivez comment affiner ces résultats.',
      runQuery: 'Exécuter', refineResults: 'Affiner les résultats', adjustQuery: 'Ajuster la requête', startNewQuery: 'Nouvelle requête',
      refineCurrent: 'Affiner le résultat actuel', refineHelp: 'Utilisez ce champ pour affiner les résultats actuels. Lancez une nouvelle requête pour une autre question.',
      results: 'Résultats', save: 'Enregistrer', saveQuery: 'Enregistrer la requête', saved: 'Enregistré', exportExcel: 'Exporter vers Excel', excel: 'Excel', viewSql: 'Voir le SQL',
      resultsHere: "Les résultats s'afficheront ici", resultsHereSub: 'Lancez une requête pour voir les enregistrements et totaux correspondants.',
      noResults: 'Aucun résultat trouvé', noResultsSub: 'La requête a réussi, mais aucun enregistrement ne correspond à ces conditions.',
      noMatching: 'Aucun enregistrement correspondant', queryCompleted: 'La requête a été exécutée avec succès.', querySaved: 'Requête enregistrée.',
      checkingQuestion: 'Vérification de votre question…', validating: 'Validation des entités, dates et intention de la requête.',
      validatingSub: 'Cela prend généralement quelques secondes. Les résultats s’afficheront dès que la requête sera validée.',
      thanksClear: 'Merci. Votre question est claire. Récupération des résultats…',
      creatingQuery: 'Création de la requête', runningQuery: 'Exécution de la requête', preparingResults: 'Préparation des résultats',
      awaiting: 'En attente des résultats', fetching: 'Récupération des résultats…', fetchingSub: 'Construction du jeu de résultats et préparation du tableau.',
      yourAnswer: 'Votre réponse', continue: 'Continuer', cancelQuery: 'Annuler la requête',
      noSaved: 'Aucune requête enregistrée', noSavedSub: 'Enregistrez des questions utiles pour retrouver leurs résultats sans les relancer.',
      you: 'Vous', assistant: 'Assistant', tryAgain: 'Réessayer', backToSaved: 'Retour aux requêtes enregistrées',
    },
    nl: {
      dashboard: 'Dashboard', newQuery: 'Nieuwe query', savedQueries: "Opgeslagen query's", settings: 'Instellingen',
      dashboardSub: 'Overzicht van WarrantsTool-activiteit en databasegereedheid.',
      dashboardSubNoDb: 'Databasebeschikbaarheid en querytoegang.',
      noDatabase: 'Geen database beschikbaar', noDatabaseSub: "Een beheerder moet een database verbinden en analyseren voordat u query's kunt uitvoeren.", refreshStatus: 'Status vernieuwen',
      connectToStart: 'Verbind uw database om te beginnen', connectToStartSub: 'Verbind een vertrouwde bedrijfsdatabase, analyseer de structuur en stel daarna vragen in natuurlijke taal.', connectDatabaseBtn: 'Database verbinden',
      step1Title: 'Database verbinden', step1Sub: 'Koppel een vertrouwde bedrijfsdatabase zodat de agent de structuur kan lezen.',
      step2Title: 'Database analyseren', step2Sub: 'Bekijk tabellen, velden en relaties voordat u begint met query’s.',
      step3Title: 'Beginnen met query’s', step3Sub: 'Stel vragen in natuurlijke taal zodra uw database klaar is.',
      queriesRun: "Uitgevoerde query's", queriesSaved: "Opgeslagen query's", recentExports: 'Recente exports', recentActivity: 'Recente activiteit',
      activeDb: 'Actieve database', askQuestion: 'Stel een specifieke vraag', conversation: 'Gesprek',
      askQuestionSub: 'Gebruik natuurlijke taal om te beschrijven wat u wilt zien.', howToWrite: 'Een goede query schrijven',
      placeholderFirst: 'bijv. Toon alle actieve warrants voor klanten in België', placeholderRefine: 'Beschrijf hoe u deze resultaten wilt verfijnen.',
      runQuery: 'Uitvoeren', refineResults: 'Resultaten verfijnen', adjustQuery: 'Query aanpassen', startNewQuery: 'Nieuwe query',
      refineCurrent: 'Huidig resultaat verfijnen', refineHelp: 'Gebruik dit veld om de huidige resultaten te verfijnen. Start een nieuwe query voor een andere vraag.',
      results: 'Resultaten', save: 'Opslaan', saveQuery: 'Query opslaan', saved: 'Opgeslagen', exportExcel: 'Exporteren naar Excel', excel: 'Excel', viewSql: 'SQL bekijken',
      resultsHere: 'Resultaten verschijnen hier', resultsHereSub: 'Voer een query uit om overeenkomende records en totalen te zien.',
      noResults: 'Geen resultaten gevonden', noResultsSub: 'De query is geslaagd, maar geen records voldeden aan deze voorwaarden.',
      noMatching: 'Geen overeenkomende records', queryCompleted: 'De query is succesvol voltooid.', querySaved: 'Query opgeslagen.',
      checkingQuestion: 'Uw vraag controleren…', validating: 'Entiteiten, datums en query-intentie valideren.',
      validatingSub: 'Dit duurt meestal een paar seconden. We tonen resultaten zodra de query is gevalideerd.',
      thanksClear: 'Bedankt. Uw vraag is duidelijk. Resultaten ophalen…',
      creatingQuery: 'Query maken', runningQuery: 'Query uitvoeren', preparingResults: 'Resultaten voorbereiden',
      awaiting: 'Wachten op resultaten', fetching: 'Resultaten ophalen…', fetchingSub: 'Resultatenset opbouwen en de tabel voorbereiden.',
      yourAnswer: 'Uw antwoord', continue: 'Doorgaan', cancelQuery: 'Query annuleren',
      noSaved: "Nog geen opgeslagen query's", noSavedSub: 'Sla nuttige vragen op om hun resultaten terug te vinden zonder ze opnieuw uit te voeren.',
      you: 'U', assistant: 'Assistent', tryAgain: 'Opnieuw proberen', backToSaved: "Terug naar opgeslagen query's",
    },
  };
  private readonly queryHelpContent: Record<string, QueryHelpContent> = {
    en: {
      title: 'How to write a good query',
      leadTitle: 'Ask for structured data, not explanations',
      lead: 'This application retrieves records, values, totals, and columns from the database. Avoid asking for product explanations, how-to guidance, or open-ended questions that do not return structured data.',
      guidanceHead: 'Concise guidance',
      guidance: [
        { title: 'Specific records', desc: 'Ask for a specific entity, such as customers, employees, invoices, or cases.' },
        { title: 'Values', desc: 'Ask for a specific field or value, such as names, IDs, amounts, or statuses.' },
        { title: 'Totals', desc: 'Ask for counts, sums, averages, or other aggregated values.' },
        { title: 'Comparisons', desc: 'Use words like greater than, less than, equals, or between to compare values.' },
        { title: 'Filters', desc: 'Use words like where, with, or containing to narrow the results.' },
        { title: 'Dates', desc: 'Use words like this year, last month, or before to specify a time range.' },
        { title: 'Columns', desc: 'Ask for specific fields or columns when you only need a subset of the data.' },
      ],
      examplesHead: 'Good examples',
      examples: [
        { text: 'Show all customers with an active warrants sales round in 2026.', tag: 'Specific entity + filter + date' },
        { text: 'What is the total revenue for customers in the North region?', tag: 'Specific value + filter' },
        { text: 'List employees with a salary greater than €80,000.', tag: 'Specific records + comparison' },
        { text: 'Show the number of open cases created before 1 January 2026.', tag: 'Specific total + date' },
      ],
      broadHead: 'Questions that are too broad',
      broad: [
        { text: 'How does the warrants tool work?', desc: 'This is a product explanation, not a structured data request.' },
        { text: 'What are all the possible sales rounds?', desc: 'Too vague — add a filter or specification.' },
      ],
    },
    fr: {
      title: 'Comment rédiger une bonne requête',
      leadTitle: 'Demandez des données structurées, pas des explications',
      lead: 'Cette application récupère des enregistrements, des valeurs, des totaux et des colonnes depuis la base de données. Évitez les explications produit, les guides pratiques ou les questions ouvertes qui ne renvoient pas de données structurées.',
      guidanceHead: 'Conseils concis',
      guidance: [
        { title: 'Enregistrements précis', desc: 'Demandez une entité précise, comme des clients, employés, factures ou dossiers.' },
        { title: 'Valeurs', desc: 'Demandez un champ ou une valeur précise, comme des noms, ID, montants ou statuts.' },
        { title: 'Totaux', desc: 'Demandez des comptages, sommes, moyennes ou autres valeurs agrégées.' },
        { title: 'Comparaisons', desc: 'Utilisez des mots comme supérieur à, inférieur à, égal à, ou entre pour comparer des valeurs.' },
        { title: 'Filtres', desc: 'Utilisez des mots comme où, avec, ou contenant pour affiner les résultats.' },
        { title: 'Dates', desc: 'Utilisez des mots comme cette année, le mois dernier, ou avant pour préciser une période.' },
        { title: 'Colonnes', desc: 'Demandez des champs ou colonnes précis lorsque vous ne voulez qu\'une partie des données.' },
      ],
      examplesHead: 'Bons exemples',
      examples: [
        { text: 'Afficher tous les clients avec un round de vente de warrants actif en 2026.', tag: 'Entité précise + filtre + date' },
        { text: 'Quel est le chiffre d\'affaires total pour les clients de la région Nord ?', tag: 'Valeur précise + filtre' },
        { text: 'Lister les employés avec un salaire supérieur à 80 000 €.', tag: 'Enregistrements précis + comparaison' },
        { text: 'Afficher le nombre de dossiers ouverts créés avant le 1er janvier 2026.', tag: 'Total précis + date' },
      ],
      broadHead: 'Questions trop générales',
      broad: [
        { text: 'Comment fonctionne l\'outil de warrants ?', desc: 'C\'est une explication produit, pas une demande de données structurées.' },
        { text: 'Quels sont tous les rounds de vente possibles ?', desc: 'Trop vague — ajoutez un filtre ou une précision.' },
      ],
    },
    nl: {
      title: 'Een goede query schrijven',
      leadTitle: 'Vraag om gestructureerde gegevens, geen uitleg',
      lead: 'Deze applicatie haalt records, waarden, totalen en kolommen op uit de database. Vermijd productuitleg, handleidingen of open vragen die geen gestructureerde gegevens opleveren.',
      guidanceHead: 'Beknopte richtlijnen',
      guidance: [
        { title: 'Specifieke records', desc: 'Vraag naar een specifieke entiteit, zoals klanten, medewerkers, facturen of dossiers.' },
        { title: 'Waarden', desc: 'Vraag naar een specifiek veld of waarde, zoals namen, ID\'s, bedragen of statussen.' },
        { title: 'Totalen', desc: 'Vraag naar aantallen, sommen, gemiddelden of andere geaggregeerde waarden.' },
        { title: 'Vergelijkingen', desc: 'Gebruik woorden als groter dan, kleiner dan, gelijk aan, of tussen om waarden te vergelijken.' },
        { title: 'Filters', desc: 'Gebruik woorden als waar, met, of bevat om de resultaten te verfijnen.' },
        { title: 'Datums', desc: 'Gebruik woorden als dit jaar, vorige maand, of voor om een periode op te geven.' },
        { title: 'Kolommen', desc: 'Vraag naar specifieke velden of kolommen als u slechts een deel van de gegevens nodig hebt.' },
      ],
      examplesHead: 'Goede voorbeelden',
      examples: [
        { text: 'Toon alle klanten met een actieve warrants-verkoopronde in 2026.', tag: 'Specifieke entiteit + filter + datum' },
        { text: 'Wat is de totale omzet voor klanten in de regio Noord?', tag: 'Specifieke waarde + filter' },
        { text: 'Toon medewerkers met een salaris hoger dan €80.000.', tag: 'Specifieke records + vergelijking' },
        { text: 'Toon het aantal open dossiers aangemaakt vóór 1 januari 2026.', tag: 'Specifiek totaal + datum' },
      ],
      broadHead: 'Vragen die te breed zijn',
      broad: [
        { text: 'Hoe werkt de warrants-tool?', desc: 'Dit is productuitleg, geen verzoek om gestructureerde gegevens.' },
        { text: 'Wat zijn alle mogelijke verkooprondes?', desc: 'Te vaag — voeg een filter of specificatie toe.' },
      ],
    },
  };
  get queryHelp(): QueryHelpContent { return this.queryHelpContent[this.lang] ?? this.queryHelpContent['en']; }
  openQueryHelp(): void { this.showQueryHelp = true; }
  closeQueryHelp(): void { this.showQueryHelp = false; }

  constructor() {
    this.api.health().subscribe({ next: () => { this.apiOnline = true; this.cdr.detectChanges(); }, error: () => { this.apiOnline = false; this.cdr.detectChanges(); } });
    this.api.domains().subscribe({ next: response => { this.domains = response.domains; this.cdr.detectChanges(); } });
    this.reloadSavedQueries();
    this.recentExports = this.loadExportCount();
    this.activeDatabase = this.loadActiveDatabase();
    this.loadAccessUsers();
  }
  ngOnInit(): void {
    this.loadDashboard();
  }
  @HostListener('document:click', ['$event'])
  onDocumentClick(event: MouseEvent): void {
    if (this.userMenuOpen && !this.elementRef.nativeElement.contains(event.target)) this.userMenuOpen = false;
  }
  toggleUserMenu(): void { this.userMenuOpen = !this.userMenuOpen; }
  selectUser(user: AppUser): void {
    this.currentUser = user; this.userMenuOpen = false;
    if (!this.isAdmin && this.activeView === 'settings') this.newConversation();
  }
  get isAdmin(): boolean { return this.currentUser.role === 'Admin'; }
  get visibleSidebarItems(): IgSidebarItem[] { return this.isAdmin ? this.sidebarItems : this.sidebarItems.filter(item => item.id !== 'settings'); }
  get isSqlAuth(): boolean { return this.selectedAuth?.id === 'sql'; }
  onAuthChange(item: IgDropdownItem | null): void { this.selectedAuth = item; this.invalidateConnectionTest(); }
  onDatabaseTypeChange(item: IgDropdownItem | null): void { this.selectedDatabaseType = item; this.invalidateConnectionTest(); }
  invalidateConnectionTest(): void { this.connectionTestPassed = false; this.connectionError = null; }
  testConnection(): void {
    const { server, database, username, password } = this.connectForm;
    if (this.isTestingConnection || !server.trim() || !database.trim()) return;
    if (this.isSqlAuth && (!username.trim() || !password.trim())) return;
    this.isTestingConnection = true; this.connectionError = null; this.cdr.detectChanges();
    this.api.testConnection({
      server: server.trim(),
      database: database.trim(),
      auth: this.isSqlAuth ? 'sql' : 'windows',
      username: this.isSqlAuth ? username.trim() : null,
      password: this.isSqlAuth ? password : null,
    }).subscribe({
      next: () => {
        this.connectionTestPassed = true; this.isTestingConnection = false; this.cdr.detectChanges();
      },
      error: error => {
        this.connectionError = this.friendlyConnectionError(error);
        this.isTestingConnection = false; this.cdr.detectChanges();
      }
    });
  }
  private friendlyConnectionError(error: { status?: number; error?: { detail?: string } }): string {
    const detail = (error?.error?.detail || '').toLowerCase();
    if (!error?.status) return 'We couldn\'t reach the server. Check that the backend is running and try again.';
    // Check "cannot open database" before "login failed": SQL Server's error for a
    // wrong/missing database name also contains the phrase "login failed", which would
    // otherwise be misreported as a credentials problem.
    if (detail.includes('cannot open database')) return 'That database name wasn\'t found on the server. Check the spelling and try again.';
    if (detail.includes('login failed')) return 'The username or password was rejected. Check your credentials and try again.';
    if (detail.includes('network-related') || detail.includes('error locating server') || detail.includes('login timeout')) {
      return 'We couldn\'t reach that server. Check the server name and that it\'s running and accessible.';
    }
    if (detail.includes('no sql server odbc driver')) return 'No SQL Server driver is installed on this machine. Install the ODBC Driver for SQL Server and try again.';
    return 'Connection failed. Check the connection details and try again.';
  }
  connectDatabase(): void {
    const { server, database, username, password } = this.connectForm;
    if (this.isConnectingDatabase || !server.trim() || !database.trim()) return;
    if (this.isSqlAuth && (!username.trim() || !password.trim())) return;
    this.isConnectingDatabase = true; this.connectionError = null; this.cdr.detectChanges();
    this.api.connect({
      server: server.trim(),
      database: database.trim(),
      auth: this.isSqlAuth ? 'sql' : 'windows',
      username: this.isSqlAuth ? username.trim() : null,
      password: this.isSqlAuth ? password : null,
    }).subscribe({
      next: response => {
        this.activeDatabase = {
          projectName: this.connectForm.database.trim(),
          databaseId: response.database_id,
          dialect: response.dialect,
          tableCount: response.table_count,
          type: this.selectedDatabaseType?.label || 'SQL Server',
          identity: this.isSqlAuth ? this.connectForm.username.trim() : 'Windows authentication',
          connectedAt: new Date().toISOString(),
          connectedBy: this.consultantName,
          server: this.connectForm.server.trim(),
        };
        this.isConnectingDatabase = false;
        this.isEditingConnection = false;
        this.connectionTestPassed = false;
        this.resetAnalysis();
        this.persistActiveDatabase();
        this.toast?.open(`Connected to ${this.activeDatabase.projectName}.`);
        this.cdr.detectChanges();
      },
      error: error => {
        this.connectionError = this.friendlyConnectionError(error);
        this.isConnectingDatabase = false; this.cdr.detectChanges();
      }
    });
  }
  editConnection(): void { this.isEditingConnection = true; this.invalidateConnectionTest(); this.cdr.detectChanges(); }
  goToConnect(): void { this.activeView = 'settings'; this.settingsTab = 'connection'; this.isEditingConnection = false; this.cdr.detectChanges(); }
  promptDisconnect(): void { this.showDisconnectDialog = true; this.cdr.detectChanges(); }
  dismissDisconnectDialog(): void { this.showDisconnectDialog = false; this.cdr.detectChanges(); }
  confirmDisconnect(): void { this.showDisconnectDialog = false; this.disconnectDatabase(); }
  disconnectDatabase(): void {
    this.activeDatabase = null;
    this.isEditingConnection = false;
    this.resetAnalysis();
    this.cancelConnectForm();
    this.connectForm = { server: '', database: '', username: '', password: '' };
    try { localStorage.removeItem(this.activeDbKey); } catch { /* storage unavailable */ }
    this.toast?.open('Database disconnected.');
    this.cdr.detectChanges();
  }
  private clearAnalysisTimers(): void {
    this.analysisTimers.forEach(clearTimeout);
    this.analysisTimers = [];
  }
  private resetAnalysis(): void {
    this.clearAnalysisTimers();
    this.analysisStatus = 'not-started';
    this.analysisStep = 0;
    this.analysisResult = null;
    this.analysisCompletedAt = null;
    this.analysisError = null;
  }
  get analysisStatusLabel(): string {
    switch (this.analysisStatus) {
      case 'in-progress': return 'In progress';
      case 'ready': return 'Ready';
      default: return 'Not started';
    }
  }
  runAnalysis(): void {
    if (!this.activeDatabase || this.analysisStatus === 'in-progress') return;
    this.clearAnalysisTimers();
    this.analysisStatus = 'in-progress';
    this.analysisStep = 1;
    this.analysisResult = null;
    this.analysisError = null;
    this.cdr.detectChanges();
    // Walk the progress steps while the schema summary is fetched; a minimum
    // display time keeps the animation from flashing on fast responses.
    this.analysisTimers.push(setTimeout(() => { this.analysisStep = 2; this.cdr.detectChanges(); }, 900));
    this.analysisTimers.push(setTimeout(() => { this.analysisStep = 3; this.cdr.detectChanges(); }, 1800));
    const startedAt = Date.now();
    this.api.analyzeDatabase(this.activeDatabase.databaseId).subscribe({
      next: (response: AnalysisResponse) => {
        const finish = () => {
          this.clearAnalysisTimers();
          this.analysisResult = {
            tables: response.table_count,
            columns: response.column_count,
            relationships: response.relationship_count,
          };
          this.analysisCompletedAt = new Date().toISOString();
          this.analysisStatus = 'ready';
          this.cdr.detectChanges();
        };
        const elapsed = Date.now() - startedAt;
        const remaining = Math.max(0, 2200 - elapsed);
        this.analysisTimers.push(setTimeout(finish, remaining));
      },
      error: error => {
        this.clearAnalysisTimers();
        this.analysisStatus = 'not-started';
        this.analysisStep = 0;
        this.analysisError = error?.error?.detail || 'Analysis could not be completed. Check the database connection and try again.';
        this.cdr.detectChanges();
      },
    });
  }
  goToNewQuery(): void { this.newConversation(); }
  private loadActiveDatabase(): ActiveDatabase | null {
    try { const raw = localStorage.getItem(this.activeDbKey); return raw ? JSON.parse(raw) as ActiveDatabase : null; } catch { return null; }
  }
  private persistActiveDatabase(): void {
    try { localStorage.setItem(this.activeDbKey, JSON.stringify(this.activeDatabase)); } catch { /* storage unavailable */ }
  }
  closeConnectForm(): void {
    if (this.activeDatabase) { this.isEditingConnection = false; this.invalidateConnectionTest(); this.cdr.detectChanges(); }
    else this.cancelConnectForm();
  }
  maskIdentity(id: string): string {
    if (!id) return '';
    return id.slice(0, Math.min(10, id.length)) + '\u2022\u2022\u2022\u2022';
  }
  cancelConnectForm(): void {
    this.connectForm = { server: '', database: '', username: '', password: '' };
    this.selectedAuth = this.authItems[0];
    this.selectedDatabaseType = this.databaseTypeItems[0];
    this.invalidateConnectionTest();
  }
  runQuery(override?: string, skipUnrelatedCheck = false): void {
    const text = (override ?? this.question).trim(); if (!text || this.isLoading) return;
    const isClarificationAnswer = override !== undefined;
    // A brand-new question must be specific enough to answer (at least 3 words).
    if (!isClarificationAnswer && text.split(/\s+/).filter(Boolean).length < 3) {
      this.validationNotice = 'Your question is too short to answer. Please be more specific and name what you want to see — for example the customers, rounds, or banks you are interested in.';
      this.cdr.detectChanges();
      return;
    }
    this.validationNotice = null;
    // A follow-up (refinement OR clarification answer) that looks like a brand-new,
    // unrelated question: ask whether to start a new query or keep going.
    if (!skipUnrelatedCheck && this.messages.length > 0 && this.looksUnrelatedFollowUp(text)) {
      this.pendingRefineText = text;
      this.pendingWasClarification = isClarificationAnswer;
      this.showUnrelatedDialog = true;
      this.cdr.detectChanges();
      return;
    }
    this.processing = isClarificationAnswer;
    this.justSaved = false;
    this.currentResultSaved = false;
    if (!isClarificationAnswer) this.question = '';
    this.submittedQuestion = text;
    let displayText = text;
    if (isClarificationAnswer && this.lastResult?.status === 'clarify') {
      const label = this.clarifyOptionText(text, this.cleanMessage(this.lastResult.message ?? null));
      if (label) displayText = `${text.trim().toUpperCase()} — ${label}`;
    }
    this.messages.push({ role: 'user', text: displayText }); this.isLoading = true; this.lastError = null; this.cdr.detectChanges();
    const apiText = this.lang === 'en' ? text : `${text}\n\n(Please answer in ${this.langNames[this.lang]}.)`;
    this.api.converse(apiText, this.domain, this.sessionId).subscribe({
      next: response => {
        this.sessionId = response.session_id;
        this.lastResult = response.result;
        this.applyResult(response.result);
        this.messages.push({ role: 'assistant', text: this.assistantCopy(response.result), result: response.result });
        this.isLoading = false; this.apiOnline = true; this.cdr.detectChanges();
        this.refreshConversations();
      },
      error: error => {
        this.lastResult = null;
        this.applyResult(null);
        this.lastError = error?.error?.detail || 'The API could not answer this request. Check that the backend is running.';
        this.messages.push({ role: 'assistant', text: this.lastError ?? '' });
        this.isLoading = false; this.apiOnline = false; this.cdr.detectChanges();
      }
    });
  }
  onEnter(event: Event): void {
    const keyboardEvent = event as KeyboardEvent;
    if (keyboardEvent.shiftKey) return;
    keyboardEvent.preventDefault();
    this.runQuery();
  }
  continueClarification(): void {
    const answer = this.clarifyAnswer.trim(); if (!answer || this.isLoading) return;
    this.clarifyAnswer = '';
    this.runQuery(answer);
  }
  cancelClarification(): void {
    this.clarifyAnswer = ''; this.lastResult = null; this.lastError = null; this.applyResult(null); this.cdr.detectChanges();
  }
  usePrompt(prompt: string): void { this.question = prompt; }
  onLanguageChange(): void { this.cdr.detectChanges(); }
  onSidebarItemClick(event: IgSidebarItemEvent): void {
    const id = event.item.id;
    if (!id) return;
    // Only warn about unsaved work when leaving the new query window.
    if (this.activeView === 'workspace' && this.hasUnsavedResult) {
      this.pendingNavId = id;
      this.showLeaveDialog = true;
      this.cdr.detectChanges();
      return;
    }
    this.navigateTo(id);
  }
  private navigateTo(id: string): void {
    switch (id) {
      case 'dashboard': this.activeView = 'dashboard'; this.loadDashboard(); break;
      case 'workspace': this.newConversation(); break;
      case 'saved': this.activeView = 'saved'; this.refreshConversations(); break;
      case 'settings': this.activeView = 'settings'; break;
    }
    this.cdr.detectChanges();
  }
  get queriesSaved(): number { return this.savedQueries.length; }
  loadDashboard(): void {
    this.dashboardLoading = true; this.dashboardError = false; this.cdr.detectChanges();
    this.api.conversations().subscribe({
      next: convs => {
        this.conversations = convs.sort((a, b) => (b.updated_at || '').localeCompare(a.updated_at || ''));
        this.queriesRun = convs.reduce((n, c) => n + (c.turn_count || 0), 0);
        this.dashboardLoading = false; this.cdr.detectChanges();
      },
      error: () => { this.dashboardLoading = false; this.dashboardError = true; this.cdr.detectChanges(); },
    });
  }
  get hasUnsavedResult(): boolean { return this.canSave && !this.isCurrentSaved; }
  leaveAndSave(): void { this.showLeaveDialog = false; this.pendingNewAfterSave = true; this.saveCurrentQuery(); }
  leaveWithoutSaving(): void {
    this.showLeaveDialog = false; this.pendingNewAfterSave = false;
    const target = this.pendingNavId ?? 'workspace'; this.pendingNavId = null;
    this.newConversation();
    if (target !== 'workspace') this.navigateTo(target);
  }
  dismissLeaveDialog(): void { this.showLeaveDialog = false; this.pendingNewAfterSave = false; this.pendingNavId = null; this.cdr.detectChanges(); }
  newConversation(): void { this.sessionId = null; this.messages = []; this.question = ''; this.submittedQuestion = null; this.clarifyAnswer = ''; this.processing = false; this.justSaved = false; this.validationNotice = null; this.lastResult = null; this.lastError = null; this.applyResult(null); this.activeView = 'workspace'; }
  openConversation(summary: ConversationSummary): void { this.api.conversation(summary.session_id).subscribe({ next: record => this.showConversation(record), error: () => this.toast?.open('Could not load that conversation.') }); }
  deleteConversation(event: Event, summary: ConversationSummary): void { event.stopPropagation(); this.api.endConversation(summary.session_id).subscribe({ next: () => { if (this.sessionId === summary.session_id) this.newConversation(); this.refreshConversations(); }, error: () => this.toast?.open('Could not remove that conversation.') }); }
  refreshConversations(): void { this.api.conversations().subscribe({ next: conversations => { this.conversations = conversations.sort((a, b) => (b.updated_at || '').localeCompare(a.updated_at || '')); this.cdr.detectChanges(); } }); }
  private reloadSavedQueries(): void {
    try {
      const raw = localStorage.getItem(this.savedKey) || '[]';
      const parsed = JSON.parse(raw);
      if (!Array.isArray(parsed)) throw new Error('Corrupt saved queries');
      this.savedQueries = parsed;
      this.savedError = false;
    } catch {
      this.savedQueries = [];
      this.savedError = true;
    }
  }
  retrySavedQueries(): void { this.reloadSavedQueries(); this.cdr.detectChanges(); }
  private persistSavedQueries(): void { localStorage.setItem(this.savedKey, JSON.stringify(this.savedQueries)); }
  get canSave(): boolean { return !this.isLoading && !!this.lastResult && this.lastResult.status === 'sql'; }
  get isCurrentSaved(): boolean { return this.currentResultSaved; }
  get originalQuestion(): string { return this.messages.find(m => m.role === 'user')?.text || this.submittedQuestion || this.lastResult?.question || ''; }
  // Heuristic: does a refine-box message look like a new, unrelated question
  // rather than a refinement of the current results?
  private looksUnrelatedFollowUp(text: string): boolean {
    const t = text.trim().toLowerCase();
    const words = t.split(/\s+/).filter(Boolean);
    if (words.length < 4) return false; // short → almost always a refinement
    const cues = ['only', 'just', 'exclude', 'include', 'add', 'remove', 'also', 'and', 'without', 'sort', 'order', 'filter', 'group', 'limit', 'top', 'more', 'less', 'instead', 'keep', 'same', 'these', 'those', 'them', 'rename', 'show the same'];
    if (cues.some(c => t === c || t.startsWith(c + ' '))) return false;
    const stop = new Set(['the', 'a', 'an', 'of', 'for', 'in', 'on', 'to', 'me', 'give', 'show', 'all', 'list', 'with', 'and', 'or', 'is', 'are', 'that', 'this', 'from', 'by', 'get', 'please', 'i', 'want', 'need', 'can', 'you', 'my', 'our']);
    const keywords = (s: string) => new Set(s.toLowerCase().split(/[^a-zà-ÿ0-9]+/).filter(w => w.length > 2 && !stop.has(w)));
    const orig = keywords(this.originalQuestion);
    const cur = keywords(t);
    if (!orig.size || !cur.size) return false;
    let overlap = 0; cur.forEach(w => { if (orig.has(w)) overlap++; });
    return overlap / cur.size < 0.2; // shares almost nothing with the original
  }
  keepRefining(): void {
    this.showUnrelatedDialog = false;
    const t = this.pendingRefineText || ''; this.pendingRefineText = null;
    const wasClarify = this.pendingWasClarification; this.pendingWasClarification = false;
    if (wasClarify) {
      this.runQuery(t, true); // send as a clarification answer, skip the check
    } else {
      this.question = t;
      this.runQuery(undefined, true);
    }
  }
  startNewFromRefine(): void {
    this.showUnrelatedDialog = false;
    const t = this.pendingRefineText || ''; this.pendingRefineText = null;
    this.pendingWasClarification = false;
    this.newConversation();
    this.question = t;
    this.runQuery(undefined, true);
  }
  dismissUnrelatedDialog(): void { this.showUnrelatedDialog = false; this.pendingRefineText = null; this.pendingWasClarification = false; this.cdr.detectChanges(); }
  saveCurrentQuery(): void {
    if (!this.lastResult) return;
    if (this.isCurrentSaved) { this.toast?.open('This query is already saved.'); return; }
    this.saveName = this.originalQuestion;
    this.saveNameError = null;
    this.showSaveDialog = true;
    this.cdr.detectChanges();
  }
  confirmSaveQuery(): void {
    if (!this.lastResult) { this.showSaveDialog = false; return; }
    const name = this.saveName.trim();
    if (!name) return;
    if (this.savedQueries.some(q => (q.name || '').trim().toLowerCase() === name.toLowerCase())) {
      this.saveNameError = 'This name is already in use. Please choose another name.';
      this.cdr.detectChanges();
      return;
    }
    const item: SavedQuery = {
      id: (globalThis.crypto?.randomUUID?.() ?? String(Date.now())),
      name,
      question: this.originalQuestion,
      savedAt: new Date().toISOString(),
      result: this.lastResult,
    };
    this.savedQueries = [item, ...this.savedQueries];
    this.persistSavedQueries();
    this.showSaveDialog = false;
    this.saveName = '';
    this.saveNameError = null;
    this.justSaved = true;
    if (this.pendingNewAfterSave) {
      this.pendingNewAfterSave = false;
      const target = this.pendingNavId ?? 'workspace'; this.pendingNavId = null;
      this.newConversation();
      if (target !== 'workspace') this.navigateTo(target);
    }
    this.cdr.detectChanges();
  }
  cancelSaveDialog(): void { this.showSaveDialog = false; this.saveName = ''; this.pendingNewAfterSave = false; this.pendingNavId = null; this.cdr.detectChanges(); }
  openSavedQuery(item: SavedQuery): void {
    this.openedSaved = item;
    this.applyResult(item.result);
    this.activeView = 'savedDetail';
    this.cdr.detectChanges();
  }
  backToSaved(): void {
    this.openedSaved = null;
    this.applyResult(null);
    this.activeView = 'saved';
    this.cdr.detectChanges();
  }
  deleteSavedQuery(event: Event, item: SavedQuery): void {
    event.stopPropagation();
    this.pendingDeleteQuery = item;
    this.showDeleteQueryDialog = true;
    this.cdr.detectChanges();
  }
  dismissDeleteQueryDialog(): void { this.showDeleteQueryDialog = false; this.pendingDeleteQuery = null; this.cdr.detectChanges(); }
  confirmDeleteSavedQuery(): void {
    const item = this.pendingDeleteQuery;
    this.showDeleteQueryDialog = false; this.pendingDeleteQuery = null;
    if (!item) { this.cdr.detectChanges(); return; }
    this.savedQueries = this.savedQueries.filter(q => q.id !== item.id);
    this.persistSavedQueries();
    this.cdr.detectChanges();
  }
  resultStatus(result: QueryResult): QueryStatus { return result.status; }
  resultColumns(result: QueryResult): string[] { return result.sample_rows.length ? Object.keys(result.sample_rows[0]) : []; }
  get showNotice(): boolean { return !this.isLoading && !!this.lastResult && this.lastResult.status !== 'sql' && this.lastResult.status !== 'clarify' && this.lastResult.status !== 'no_data'; }
  get isClarifying(): boolean { return !this.isLoading && this.lastResult?.status === 'clarify'; }
  get clarifyQuestion(): string { return this.isClarifying ? (this.cleanMessage(this.lastResult?.message ?? null) || '') : ''; }
  get isNoData(): boolean { return !this.isLoading && this.lastResult?.status === 'no_data'; }
  get noticeTone(): 'info' | 'danger' {
    return this.lastResult && (this.lastResult.status === 'refused' || this.lastResult.status === 'error' || this.lastResult.status === 'timeout') ? 'danger' : 'info';
  }
  get noticeIcon(): string { return this.noticeTone === 'danger' ? 'error_outline' : 'info'; }
  get noticeMessage(): string { return this.cleanMessage(this.lastResult?.message ?? null) || 'This question could not be turned into a database query.'; }
  get noticeHint(): string {
    switch (this.lastResult?.status) {
      case 'clarify': return 'See the documentation for guidance on how to formulate a query.';
      case 'refused': return 'Only read-only questions about your data are supported.';
      case 'unavailable': return 'This information is not available in the current database.';
      case 'no_data': return 'The query ran successfully but returned no matching records.';
      case 'timeout': return 'The query took too long to run. Try narrowing it down.';
      case 'error': return 'Something went wrong while answering this request. Please try again.';
      default: return 'See the documentation for guidance on how to formulate a query.';
    }
  }
  private applyResult(result: QueryResult | null): void {
    const rows = result?.sample_rows ?? [];
    this.rowData = rows;
    this.columnDefs = rows.length ? Object.keys(rows[0]).map(key => ({ field: key, headerName: key })) : [];
  }
  exportToExcel(result: QueryResult): void {
    const rows = result.sample_rows ?? [];
    if (!rows.length) { this.toast?.open('There are no rows to export.'); return; }
    const columns = Object.keys(rows[0]);
    const data = rows.map(row => {
      const projected: Record<string, unknown> = {};
      for (const column of columns) projected[column] = row[column];
      return projected;
    });
    const fileName = `brainbots-results-${new Date().toISOString().slice(0, 10)}`;
    this.excelExporter.exportData(data, new IgxExcelExporterOptions(fileName));
  }
  private loadExportCount(): number {
    const n = Number(localStorage.getItem(this.exportKey));
    return Number.isFinite(n) && n > 0 ? n : 0;
  }
  trackByIndex(index: number): number { return index; }
  private showConversation(record: ConversationRecord): void { this.sessionId = record.session_id; this.messages = []; record.turns.forEach(turn => { this.messages.push({ role: 'user', text: turn.question }); this.messages.push({ role: 'assistant', text: this.assistantCopy(turn.result), result: turn.result }); }); const lastTurn = record.turns[record.turns.length - 1]; this.lastResult = lastTurn ? lastTurn.result : null; this.applyResult(this.lastResult); this.question = ''; this.lastError = null; this.activeView = 'workspace'; this.cdr.detectChanges(); }
  // Maps a short clarification answer (e.g. "A" or "1") to the option text
  // from the assistant's clarifying question so the thread shows both.
  private clarifyOptionText(answer: string, question: string): string | null {
    const key = answer.trim().toUpperCase();
    if (!/^([A-Z]|\d{1,2})$/.test(key)) return null;
    return this.parseClarifyOptions(question)[key] ?? null;
  }
  private parseClarifyOptions(question: string): Record<string, string> {
    const options: Record<string, string> = {};
    // Markers like "(A)", "A)", "A.", "A:", "(1)", "1)", "1." — letter or number.
    const markerRe = /(?:^|[\s,;])\(?([A-Za-z]|\d{1,2})[).:]/g;
    const markers: { key: string; start: number; end: number }[] = [];
    let m: RegExpExecArray | null;
    while ((m = markerRe.exec(question)) !== null) {
      markers.push({ key: m[1].toUpperCase(), start: m.index, end: markerRe.lastIndex });
    }
    for (let i = 0; i < markers.length; i++) {
      const from = markers[i].end;
      const to = i + 1 < markers.length ? markers[i + 1].start : question.length;
      let text = question.slice(from, to).replace(/\s+/g, ' ').trim();
      const q = text.indexOf('?');
      if (q !== -1) text = text.slice(0, q).trim();
      text = text.replace(/^or\s+/i, '').replace(/\s+or$/i, '').replace(/[,;]\s*$/, '').trim();
      if (text) options[markers[i].key] = text;
    }
    return options;
  }
  private assistantCopy(result: QueryResult): string {
    if (result.status === 'no_data') return this.t('queryCompleted');
    if (result.status === 'sql') return result.explanation || 'I translated the request into a read-only SQL query.';
    return this.cleanMessage(result.message) || this.cleanMessage(result.error) || 'The request did not produce a query.';
  }
  // Guards against the backend leaking a raw JSON envelope (e.g. when the model
  // emits unescaped quotes) by extracting the inner "message" text.
  private cleanMessage(text: string | null): string {
    const t = (text || '').trim();
    if (!t.startsWith('{') || !t.includes('"message"')) return t;
    try {
      const obj = JSON.parse(t);
      if (obj && typeof obj.message === 'string') return obj.message;
    } catch { /* fall through to regex */ }
    const m = t.match(/"message"\s*:\s*"([\s\S]*)"\s*}\s*$/);
    return m ? m[1].replace(/\\"/g, '"').replace(/\\n/g, '\n') : t;
  }
}
