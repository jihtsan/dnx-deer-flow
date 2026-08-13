import type { LucideIcon } from "lucide-react";

export interface Translations {
  // Locale meta
  locale: {
    localName: string;
  };

  // Common
  common: {
    home: string;
    settings: string;
    delete: string;
    edit: string;
    rename: string;
    renameFailed: string;
    share: string;
    openInNewWindow: string;
    close: string;
    more: string;
    search: string;
    loadMore: string;
    download: string;
    thinking: string;
    artifacts: string;
    public: string;
    custom: string;
    notAvailableInDemoMode: string;
    loading: string;
    version: string;
    lastUpdated: string;
    code: string;
    preview: string;
    cancel: string;
    save: string;
    install: string;
    create: string;
    import: string;
    export: string;
    exportAsMarkdown: string;
    exportAsJSON: string;
    exportSuccess: string;
    exportFailed: string;
    regenerate: string;
    editAndRerun: string;
    updateAndRerun: string;
    editRerunWarning: string;
    branch: string;
    showArtifacts: string;
    browser: string;
    showBrowser: string;
  };

  runDuration: {
    reasoning: string;
    working: string;
    completedIn: (duration: string) => string;
    description: string;
    lessThanSecond: string;
    hours: (value: number) => string;
    minutes: (value: number) => string;
    seconds: (value: number) => string;
    separator: string;
  };

  home: {
    docs: string;
    blog: string;
  };

  // Welcome
  welcome: {
    greeting: string;
    description: string;
    createYourOwnSkill: string;
    createYourOwnSkillDescription: string;
  };

  // Clipboard
  clipboard: {
    copyToClipboard: string;
    copiedToClipboard: string;
    failedToCopyToClipboard: string;
    linkCopied: string;
  };

  artifactEditing: {
    unsaved: string;
    saving: string;
    saved: string;
    exit: string;
    discard: string;
    discardChanges: string;
    conflict: string;
    conflictShort: string;
    runInProgress: string;
    saveFailed: string;
  };

  artifactPreview: {
    limited: (previewSize: string, totalSize?: string) => string;
    loadFullFile: string;
    loadingFullFile: string;
    previewFailed: string;
  };

  // Citations
  citations: {
    sourcesSummary: (count: number) => string;
    citeCount: (count: number) => string;
    copyReference: (title: string) => string;
    copiedReference: (title: string) => string;
  };

  // Workspace Changes
  workspaceChanges: {
    title: string;
    editedTitle: (count: number) => string;
    badge: (count: number, additions: number, deletions: number) => string;
    viewChanges: string;
    created: string;
    modified: string;
    deleted: string;
    openFile: string;
    loading: string;
    noChanges: string;
    diffUnavailable: string;
    binaryUnavailable: string;
    largeUnavailable: string;
    sensitiveUnavailable: string;
    truncatedUnavailable: string;
    symlinkUnavailable: string;
    truncatedSummary: string;
  };

  // Input Box
  inputBox: {
    placeholder: string;
    disclaimer: string;
    createSkillPrompt: string;
    addAttachments: string;
    inputPolish: string;
    inputPolishing: string;
    inputPolishNoChanges: string;
    inputPolishFailed: string;
    inputPolishUndo: string;
    inputPolishCancel: string;
    voiceInputStartLabel: string;
    voiceInputStopLabel: string;
    voiceInputStart: string;
    voiceInputStop: string;
    voiceInputListening: string;
    voiceInputUnsupported: string;
    voiceInputPermissionDenied: string;
    voiceInputMicrophoneUnavailable: string;
    voiceInputUnsupportedLanguage: string;
    voiceInputNetworkError: string;
    voiceInputNoSpeech: string;
    voiceInputFailed: string;
    mode: string;
    flashMode: string;
    flashModeDescription: string;
    reasoningMode: string;
    reasoningModeDescription: string;
    proMode: string;
    proModeDescription: string;
    ultraMode: string;
    ultraModeDescription: string;
    reasoningEffort: string;
    reasoningEffortMinimal: string;
    reasoningEffortMinimalDescription: string;
    reasoningEffortLow: string;
    reasoningEffortLowDescription: string;
    reasoningEffortMedium: string;
    reasoningEffortMediumDescription: string;
    reasoningEffortHigh: string;
    reasoningEffortHighDescription: string;
    searchModels: string;
    surpriseMe: string;
    surpriseMePrompt: string;
    followupLoading: string;
    followupConfirmTitle: string;
    followupConfirmDescription: string;
    followupConfirmAppend: string;
    followupConfirmReplace: string;
    suggestionPlaceholderRequired: string;
    goalCommandDescription: string;
    compactCommandDescription: string;
    goalLabel: string;
    goalContinuing: string;
    goalContinuationTooltip: string;
    goalSet: string;
    goalCleared: string;
    goalNone: string;
    goalActive: string;
    goalFailed: string;
    goalTooLong: string;
    goalLengthCounter: string;
    compactSuccess: string;
    compactSkipped: string;
    compactFailed: string;
    suggestions: {
      suggestion: string;
      prompt: string;
      icon: LucideIcon;
    }[];
    suggestionsCreate: (
      | {
          suggestion: string;
          prompt: string;
          icon: LucideIcon;
        }
      | {
          type: "separator";
        }
    )[];
    pleaseWaitStreaming: string;
  };

  // Sidebar
  sidebar: {
    recentChats: string;
    newChat: string;
    chats: string;
    demoChats: string;
    agents: string;
    knowledgeBase: string;
    scheduledTasks: string;
    agentsDisabledTooltip: string;
    channels: string;
  };

  // Knowledge base
  knowledgeBase: {
    title: string;
    description: string;
    loading: string;
    requestErrorTitle: string;
    requestErrorDescription: string;
    retry: string;
    offlineTitle: string;
    offlineDescription: string;
    checkAgain: string;
    createErrorTitle: string;
    retryCreate: string;
    emptyTitle: string;
    emptyDescription: string;
    scopeName: string;
    scopeNamePlaceholder: string;
    scopeDescription: string;
    scopeDescriptionPlaceholder: string;
    create: string;
    creating: string;
    createUnavailable: string;
    save: string;
    saving: string;
    saveSuccess: string;
    scopeStableId: string;
    scopeEnabled: string;
    scopeDisabled: string;
    enabledControl: string;
    documentsTotal: string;
    documentsReady: string;
    documentsFailed: string;
    disabledRetrieval: string;
    enableUnavailable: string;
    statusTitle: string;
    diagnosticsTitle: string;
    singleWorkspace: string;
    enterpriseLabel: string;
    documentsProcessing: string;
    tabs: {
      documents: string;
      graph: string;
      retrieval: string;
    };
    graph: {
      title: string;
      nodes: string;
      edges: string;
      popularGroups: string;
      showAll: string;
      searchPlaceholder: string;
      noSearchResults: string;
      liveData: string;
      loading: string;
      errorTitle: string;
      empty: string;
      unavailable: string;
      truncated: string;
      retry: string;
      refresh: string;
      reset: string;
      zoomIn: string;
      zoomOut: string;
      hint: string;
      degree: string;
      relations: string;
      source: string;
      close: string;
      legend: Record<
        | "document"
        | "chunk"
        | "org"
        | "person"
        | "product"
        | "tech"
        | "concept"
        | "place",
        string
      >;
    };
    retrieval: {
      modesTitle: string;
      run: string;
      running: string;
      queryPlaceholder: string;
      resultTitle: string;
      sourcesTitle: string;
      contextTitle: string;
      hitEntities: string;
      hitRelations: string;
      referencesTitle: string;
      keywordsTitle: string;
      highLevelKeywords: string;
      lowLevelKeywords: string;
      liveData: string;
      unavailable: string;
      errorTitle: string;
      empty: string;
      retry: string;
      entitiesCount: string;
      relationsCount: string;
      chunksCount: string;
      referencesCount: string;
      modes: Record<"naive" | "local" | "global" | "hybrid" | "mix", string>;
      modeHints: Record<
        "naive" | "local" | "global" | "hybrid" | "mix",
        string
      >;
    };
    status: Record<
      "unconfigured" | "disabled" | "offline" | "incompatible" | "ready",
      string
    >;
    labels: {
      workspaceMode: string;
      expectedTag: string;
      expectedCommit: string;
      expectedCoreVersion: string;
      expectedApiVersion: string;
      observedCoreVersion: string;
      observedApiVersion: string;
      serviceStatus: string;
    };
    documents: {
      title: string;
      description: string;
      uploadTitle: string;
      fileLabel: string;
      selectedFile: string;
      noFile: string;
      upload: string;
      uploading: string;
      retryUpload: string;
      accepted: string;
      alreadyAccepted: string;
      uploadErrorTitle: string;
      featureDisabled: string;
      scopeDisabled: string;
      dataPlaneUnavailable: string;
      listTitle: string;
      loading: string;
      listErrorTitle: string;
      retryList: string;
      emptyTitle: string;
      emptyDescription: string;
      trackingId: string;
      trackingPending: string;
      ingestionJobId: string;
      updatedAt: string;
      failureReason: string;
      catalogTitle: string;
      detailEmpty: string;
      fileType: string;
      uploadedAt: string;
      retryWaiting: string;
      errorType: string;
      attempts: string;
      lastAttempt: string;
      nextRetry: string;
      notAvailable: string;
      manualRetry: string;
      retrying: string;
      retryErrorTitle: string;
      deleteDocument: string;
      deletingDocument: string;
      deleteDialogTitle: string;
      deleteDialogDescription: string;
      deleteConfirm: string;
      deleteCancel: string;
      deleteErrorTitle: string;
      deleteErrorDescription: string;
      retryWaitTitle: string;
      retryWaitDescription: string;
      directoryTitle: string;
      newDirectory: string;
      createDirectory: string;
      createChildDirectory: string;
      renameDirectory: string;
      deleteDirectory: string;
      directoryName: string;
      directoryErrorTitle: string;
      uploadDestination: string;
      moveTo: string;
      moving: string;
      managedSource: string;
      remoteSource: string;
      remoteNotice: string;
      contentLength: string;
      noDocumentsInDirectory: string;
      breadcrumbRoot: string;
      refresh: string;
      pipelineTitle: string;
      diagnosticsTitle: string;
      pipeline: Record<"upload" | "queue" | "index" | "ready", string>;
      pipelineDetail: Record<
        | "uploaded"
        | "pending"
        | "queued"
        | "waiting"
        | "indexing"
        | "indexed"
        | "retrying"
        | "ready"
        | "failed",
        string
      >;
      progressStage: Record<
        | "pending"
        | "parsing"
        | "analyzing"
        | "processing"
        | "preprocessed"
        | "processed"
        | "failed",
        string
      >;
      progressPhase: Record<
        "contentParsing" | "multimodal" | "entityExtraction" | "graphWriting",
        string
      >;
      stageElapsed: string;
      chunksCount: string;
      status: Record<"pending" | "indexing" | "ready" | "failed", string>;
    };
  };

  // Scheduled tasks
  scheduledTasks: {
    scheduleType: { cron: string; once: string };
    preset: {
      label: string;
      hourly: string;
      daily: string;
      weekly: string;
      monthly: string;
      custom: string;
    };
    fields: {
      minute: string;
      time: string;
      weekday: string;
      dayOfMonth: string;
      cron: string;
      cronPlaceholder: string;
      runAt: string;
      timezone: string;
    };
    weekdays: {
      mon: string;
      tue: string;
      wed: string;
      thu: string;
      fri: string;
      sat: string;
      sun: string;
    };
    preview: string;
    cronHelp: string;
    create: {
      title: string;
      taskTitle: string;
      prompt: string;
      submit: string;
      fillRequired: string;
    };
    context: {
      fresh: string;
      reuse: string;
      threadIdPlaceholder: string;
    };
    filters: {
      allStatuses: string;
      enabled: string;
      paused: string;
      completed: string;
      failed: string;
      allTypes: string;
      cron: string;
      once: string;
    };
    detail: {
      contextMode: string;
      thread: string;
      lastThread: string;
      schedule: string;
      nextRun: string;
      lastRun: string;
      lastRunId: string;
      lastError: string;
      runsCount: string;
      runsCountOne: string;
      noRuns: string;
      noSelection: string;
      filteredByThread: string;
      loadFailed: string;
    };
    actions: {
      edit: string;
      cancelEdit: string;
      pause: string;
      resume: string;
      trigger: string;
      delete: string;
    };
    deleteConfirm: string;
    errors: {
      create: string;
      update: string;
      pause: string;
      resume: string;
      trigger: string;
      delete: string;
    };
    edit: {
      titlePlaceholder: string;
      promptPlaceholder: string;
      submit: string;
    };
    status: {
      enabled: string;
      paused: string;
      running: string;
      completed: string;
      failed: string;
      cancelled: string;
    };
    runTrigger: { scheduled: string; manual: string };
    runStatus: {
      queued: string;
      running: string;
      success: string;
      failed: string;
      skipped: string;
      interrupted: string;
    };
    recipes: {
      label: string;
      trending: { title: string; desc: string };
      news: { title: string; desc: string };
      issues: { title: string; desc: string };
      weekly: { title: string; desc: string };
    };
  };

  // Agents
  agents: {
    title: string;
    description: string;
    newAgent: string;
    emptyTitle: string;
    emptyDescription: string;
    featureDisabledTitle: string;
    featureDisabledDescription: string;
    chat: string;
    delete: string;
    deleteConfirm: string;
    deleteSuccess: string;
    newChat: string;
    createPageTitle: string;
    createPageSubtitle: string;
    nameStepTitle: string;
    nameStepHint: string;
    nameStepPlaceholder: string;
    nameStepContinue: string;
    nameStepInvalidError: string;
    nameStepAlreadyExistsError: string;
    nameStepNetworkError: string;
    nameStepCheckError: string;
    nameStepCheckErrorWithDetail: string;
    nameStepApiDisabledError: string;
    nameStepBootstrapMessage: string;
    save: string;
    saving: string;
    saveRequested: string;
    saveHint: string;
    saveCommandMessage: string;
    agentCreatedPendingRefresh: string;
    more: string;
    agentCreated: string;
    startChatting: string;
    backToGallery: string;
    settings: string;
    settingsTitle: string;
    settingsDescription: string;
    settingsModel: string;
    settingsModelDefault: string;
    settingsTemperature: string;
    settingsTemperatureHint: string;
    settingsMaxTokens: string;
    settingsMaxTokensPlaceholder: string;
    settingsThinking: string;
    settingsThinkingOn: string;
    settingsThinkingOff: string;
    settingsReasoningEffort: string;
    settingsInherit: string;
    settingsSaved: string;
    settingsInvalidTemperature: string;
    settingsInvalidMaxTokens: string;
  };

  // Breadcrumb
  breadcrumb: {
    workspace: string;
    chats: string;
    knowledgeBase: string;
  };

  // Workspace
  workspace: {
    officialWebsite: string;
    githubTooltip: string;
    settingsAndMore: string;
    visitGithub: string;
    reportIssue: string;
    contactUs: string;
    about: string;
    logout: string;
    gatewayUnavailable: string;
    gatewayUnavailableRetrying: string;
  };

  // Conversation
  conversation: {
    noMessages: string;
    startConversation: string;
    branchCreated: string;
    branchFailed: string;
    streamReplayGap: string;
  };

  // Chats
  chats: {
    searchChats: string;
    loadMoreToSearch: string;
    loadingMore: string;
    loadOlderChats: string;
    pinChat: string;
    unpinChat: string;
    pinChatFailed: string;
  };

  // Sidecar
  sidecar: {
    title: string;
    open: string;
    close: string;
    delete: string;
    deleteConfirm: string;
    deleteSuccess: string;
    deleteFailed: string;
    addToConversation: string;
    askInSideChat: string;
    reference: string;
    selectedTextFragment: string;
    selectedTextFragments: string;
    clearReferences: string;
    emptyTitle: string;
    emptyDescription: string;
    placeholder: string;
    send: string;
    sendFailed: string;
    noContext: string;
    continuing: string;
    selectionCrossesMessages: string;
  };

  // Channels
  channels: {
    title: string;
    connect: string;
    modify: string;
    reconnect: string;
    disconnect: string;
    connected: string;
    notConnected: string;
    pending: string;
    revoked: string;
    disabled: string;
    unconfigured: string;
    unavailable: string;
    unavailableShort: string;
    setupTitle: (name: string) => string;
    setupEditTitle: (name: string) => string;
    setupDescription: string;
    saveAndConnect: string;
    saveChanges: string;
    descriptions: Record<string, string>;
    connectedAs: (name: string) => string;
  };

  // Page titles (document title)
  pages: {
    appName: string;
    chats: string;
    newChat: string;
    untitled: string;
  };

  // Tool calls
  toolCalls: {
    moreSteps: (count: number) => string;
    lessSteps: string;
    executeCommand: string;
    presentFiles: string;
    needYourHelp: string;
    useTool: (toolName: string) => string;
    searchForRelatedInfo: string;
    searchForRelatedImages: string;
    searchFor: (query: string) => string;
    searchForRelatedImagesFor: (query: string) => string;
    searchOnWebFor: (query: string) => string;
    viewWebPage: string;
    listFolder: string;
    readFile: string;
    writeFile: string;
    clickToViewContent: string;
    writeTodos: string;
    skillInstallTooltip: string;
    browserNavigate: (url: string) => string;
    browserNavigateGeneric: string;
    browserClick: string;
    browserType: string;
    browserSnapshot: string;
    browserGetText: string;
    browserBack: string;
    browserScreenshot: string;
    browserClose: string;
  };

  humanInput: {
    answered: string;
    pending: string;
    readOnly: string;
    otherLabel: string;
    otherPlaceholder: string;
    submit: string;
    emptyError: string;
    requiredError: string;
    requiredA11yLabel: string;
    selectPlaceholder: string;
    answeredValue: (value: string) => string;
  };

  // Uploads
  uploads: {
    uploading: string;
    uploadingFiles: string;
    limitsHint: (
      maxFiles: number,
      maxFileSize: string,
      maxTotalSize: string,
    ) => string;
    filesTooLarge: (files: string, maxFileSize: string) => string;
    tooManyFiles: (count: number, maxFiles: number) => string;
    totalSizeTooLarge: (count: number, maxTotalSize: string) => string;
  };

  // Subtasks
  subtasks: {
    subtask: string;
    executing: (count: number) => string;
    in_progress: string;
    completed: string;
    failed: string;
  };

  // Token Usage
  tokenUsage: {
    title: string;
    label: string;
    input: string;
    output: string;
    total: string;
    view: string;
    unavailable: string;
    unavailableShort: string;
    collecting: string;
    note: string;
    presets: {
      off: string;
      summary: string;
      perTurn: string;
      debug: string;
    };
    presetDescriptions: {
      off: string;
      summary: string;
      perTurn: string;
      debug: string;
    };
    finalAnswer: string;
    stepTotal: string;
    sharedAttribution: string;
    subagent: (description: string) => string;
    startTodo: (content: string) => string;
    completeTodo: (content: string) => string;
    updateTodo: (content: string) => string;
    removeTodo: (content: string) => string;
  };

  contextUsage: {
    label: string;
    title: string;
    badgeAriaLabel: (percentage: string) => string;
  };

  // Shortcuts
  shortcuts: {
    searchActions: string;
    noResults: string;
    actions: string;
    keyboardShortcuts: string;
    keyboardShortcutsDescription: string;
    openCommandPalette: string;
    toggleSidebar: string;
  };

  // Settings
  settings: {
    title: string;
    description: string;
    sections: {
      account: string;
      appearance: string;
      channels: string;
      integrations: string;
      memory: string;
      tools: string;
      skills: string;
      notification: string;
      about: string;
    };
    memory: {
      title: string;
      description: string;
      empty: string;
      rawJson: string;
      exportButton: string;
      exportSuccess: string;
      importButton: string;
      importConfirmTitle: string;
      importConfirmDescription: string;
      importFileLabel: string;
      importInvalidFile: string;
      importSuccess: string;
      manualFactSource: string;
      addFact: string;
      addFactTitle: string;
      editFactTitle: string;
      addFactSuccess: string;
      editFactSuccess: string;
      clearAll: string;
      clearAllConfirmTitle: string;
      clearAllConfirmDescription: string;
      clearAllSuccess: string;
      factDeleteConfirmTitle: string;
      factDeleteConfirmDescription: string;
      factDeleteSuccess: string;
      factContentLabel: string;
      factCategoryLabel: string;
      factConfidenceLabel: string;
      factContentPlaceholder: string;
      factCategoryPlaceholder: string;
      factConfidenceHint: string;
      factSave: string;
      factValidationContent: string;
      factValidationConfidence: string;
      noFacts: string;
      summaryReadOnly: string;
      memoryFullyEmpty: string;
      factPreviewLabel: string;
      searchPlaceholder: string;
      filterAll: string;
      filterFacts: string;
      filterSummaries: string;
      noMatches: string;
      markdown: {
        overview: string;
        userContext: string;
        work: string;
        personal: string;
        topOfMind: string;
        historyBackground: string;
        recentMonths: string;
        earlierContext: string;
        longTermBackground: string;
        updatedAt: string;
        facts: string;
        empty: string;
        table: {
          category: string;
          confidence: string;
          confidenceLevel: {
            veryHigh: string;
            high: string;
            normal: string;
            unknown: string;
          };
          content: string;
          source: string;
          createdAt: string;
          view: string;
        };
      };
    };
    appearance: {
      themeTitle: string;
      themeDescription: string;
      system: string;
      light: string;
      dark: string;
      systemDescription: string;
      lightDescription: string;
      darkDescription: string;
      languageTitle: string;
      languageDescription: string;
    };
    tools: {
      title: string;
      description: string;
      adminRequired: string;
      empty: string;
    };
    channels: {
      title: string;
      description: string;
      disabled: string;
    };
    integrations: {
      title: string;
      description: string;
      refresh: string;
      install: string;
      reinstall: string;
      installing: string;
      ready: string;
      pending: string;
      available: string;
      unavailable: string;
      connected: string;
      loadFailed: string;
      adminRequired: string;
      lark: {
        title: string;
        description: string;
        skillPack: string;
        gatewayCli: string;
        auth: string;
        sandboxRuntime: string;
        sandboxRuntimeInitContainer: string;
        sandboxRuntimeBroker: string;
        sandboxRuntimeGatewayDownload: string;
        sandboxRuntimeNotReady: string;
        notInstalled: string;
        skillsInstalled: (installed: number, expected: number) => string;
        installedVersion: (version: string) => string;
        updateAvailable: (version: string) => string;
        runtimeVersionMismatch: string;
        authNotConfigured: string;
        authConfigured: string;
        authConfiguredFor: (user: string) => string;
        connect: string;
        authStarting: string;
        checkingConnection: string;
        connectedAction: string;
        requestPermissions: string;
        alreadyConnected: string;
        changeAppButton: string;
        changeAppTitle: string;
        changeAppDescription: string;
        changeAppIdLabel: string;
        changeAppSecretLabel: string;
        changeAppAuthResetNote: string;
        changeAppSubmit: string;
        changeAppReRegister: string;
        changeAppSwitched: string;
        brandFeishu: string;
        brandLark: string;
        connectionStarted: string;
        connectionReady: string;
        authStarted: string;
        authorizationStillPending: string;
        permissionTitle: string;
        permissionDescription: string;
        authDomains: Record<
          | "approval"
          | "apps"
          | "attendance"
          | "base"
          | "calendar"
          | "contact"
          | "docs"
          | "drive"
          | "event"
          | "im"
          | "mail"
          | "markdown"
          | "mindnotes"
          | "minutes"
          | "note"
          | "okr"
          | "sheets"
          | "slides"
          | "task"
          | "vc"
          | "wiki"
          | "all",
          { label: string; description: string }
        >;
        customScopeLabel: string;
        customScopePlaceholder: string;
        customScopeDescription: string;
        openConnectionLinkTitle: string;
        openConnectionLinkDescription: string;
        openAuthLinkTitle: string;
        openAuthLinkDescription: string;
        waitingAuthTitle: string;
        waitingAuthDescription: string;
        openAuthLink: string;
        copyAuthLink: string;
        completeAuth: string;
        continueAuth: string;
        preparingAuthorization: string;
        completingAuth: string;
        authExpiresIn: (seconds: number) => string;
        installingTitle: string;
        installingDescription: string;
        installNextTitle: string;
        installNextDescription: string;
        cliNextTitle: string;
        cliNextDescription: string;
        configuredTitle: string;
        configuredDescription: string;
        connectedTitle: string;
        connectedDescription: string;
        authNextTitle: string;
        authNextDescription: string;
      };
    };
    skills: {
      title: string;
      description: string;
      createSkill: string;
      emptyTitle: string;
      emptyDescription: string;
      emptyButton: string;
      adminRequired: string;
      installAdminRequired: string;
    };
    notification: {
      title: string;
      description: string;
      requestPermission: string;
      deniedHint: string;
      testButton: string;
      testTitle: string;
      testBody: string;
      notSupported: string;
      disableNotification: string;
    };
    account: {
      profileTitle: string;
      email: string;
      role: string;
      changePasswordTitle: string;
      changePasswordDescription: string;
      ssoProvider: string;
      ssoPasswordDescription: string;
      ssoPasswordMessage: string;
      currentPassword: string;
      newPassword: string;
      confirmNewPassword: string;
      passwordMismatch: string;
      passwordTooShort: string;
      passwordChangedSuccess: string;
      networkError: string;
      updating: string;
      updatePassword: string;
      signOut: string;
    };
    acknowledge: {
      emptyTitle: string;
      emptyDescription: string;
    };
  };

  // Login / Auth
  login: {
    signInTitle: string;
    createAccountTitle: string;
    email: string;
    emailPlaceholder: string;
    password: string;
    passwordPlaceholder: string;
    rememberMe: string;
    rememberMeDescription: string;
    pleaseWait: string;
    signIn: string;
    createAccount: string;
    createAdminAccount: string;
    adminSetupRequiredTitle: string;
    adminSetupRequiredDescription: string;
    orContinueWith: string;
    ssoHint: string;
    continueWith: (provider: string) => string;
    noAccountSignUp: string;
    haveAccountSignIn: string;
    backToHome: string;
    networkError: string;
    serviceUnavailableTitle: string;
    serviceUnavailableDescription: string;
    retry: string;
    authFailed: string;
    errors: {
      sso_failed: string;
      sso_cancelled: string;
      sso_account_exists: string;
      sso_not_allowed: string;
    };
  };
}
