// Internationalization (i18n) configuration and translations.

export type Language = 'en' | 'zh';
export type TranslationKey = string;

export interface Translation {
  [key: string]: {
    [key: string]: string;
  };
}

export const translations: Translation = {
  en: {
    // Common
    'common.loading': 'Loading...',
    'common.error': 'Error',
    'common.cancel': 'Cancel',
    'common.delete': 'Delete',
    'common.back': 'Back',
    
    // Status
    'status.pending': 'Pending',
    'status.running': 'Running',
    'status.completed': 'Completed',
    'status.failed': 'Failed',
    'status.cancelled': 'Cancelled',
    'status.paused': 'Paused',
    'status.error': 'Error',
    
    // Pipeline Stages
    'stage.find': 'Find',
    'stage.analyze': 'Analyze',
    'stage.download': 'Download',
    'stage.qualify': 'Qualify',
    'stage.complete': 'Complete',
    
    // Data Classification
    'data.both': 'Both Data Types',
    'data.sequencing_only': 'Sequencing Only',
    'data.drug_only': 'Drug Response Only',
    'data.manual': 'Manual Required',
    'data.contact': 'Contact',
    'data.repository_dataset': 'DB Dataset',
    'data.embedded_dataset': 'Table Data',
    'data.acquisition_evidence': 'Evidence',
    'data.controlled_access': 'Controlled',
    'data.browser_blocked': 'Blocked',
    'data.accession_missing': 'Missing ID',
    'data.pdf_only': 'PDF Only',
    'data.no_valid_dataset': 'Invalid',
    'data.explain.repository_dataset': 'usable downloaded file',
    'data.explain.embedded_dataset': 'table/figure extracted',
    'data.explain.acquisition_evidence': 'evidence only',
    'data.explain.controlled_access': 'needs approval',
    'data.explain.browser_blocked': 'site verification blocked',
    'data.explain.accession_missing': 'missing stable ID',
    'data.explain.pdf_only': 'PDF only, no table file',
    'data.explain.no_valid_dataset': 'invalid or empty files',
    'data.explain.contact_author': 'needs author reply',
    'data.explain.both_data': 'sequencing and drug data',
    'data.explain.sequencing_only': 'sequencing only',
    'data.explain.drug_only': 'drug response only',
    'data.explain.manual_required': 'manual extraction needed',
    
    // Home Page
    'home.title': 'AI4Med',
    'home.projects': 'Projects',
    
    // Project Card
    'project.view': 'View Details',
    
    // Project Detail
    
    // Stage Card
    'stage.status': 'Status',
    'stage.progress': 'Progress',
    'stage.start_time': 'Start Time',
    'stage.end_time': 'End Time',
    'stage.duration': 'Duration',
    'stage.messages': 'Messages',
    'stage.quick_stats': 'Quick Stats',
    'stage.artifacts': 'Artifacts',
    'stage.no_messages': 'No messages yet',

    // Relative time & duration (shared utils formatters)
    'time.relative.just_now': 'just now',
    'time.relative.minutes_ago': '{{n}}m ago',
    'time.relative.hours_ago': '{{n}}h ago',
    'time.relative.days_ago': '{{n}}d ago',
    'time.duration.seconds': '{{n}}s',
    'time.duration.less_than_second': '<1s',
    'time.duration.min_sec': '{{m}}m {{s}}s',
    'time.duration.hr_min': '{{h}}h {{m}}m',
    'time.placeholder.dash': '-',
    
    // Artifacts
    'artifacts.name': 'Name',
    'artifacts.download': 'Download',
    'detail.show_all_screenshots': 'Show all screenshots ({{count}})',
    'detail.hide_screenshots': 'Hide screenshots',
    
    // Messages
    'messages.no_messages': 'No messages yet',
    
    // User Menu
    'user.account': 'Account',
    'user.language': 'Language',
    'user.logout': 'Log Out',

    // Languages
    
    // Login Page
    'login.error': 'Invalid email or password',
    'login.error_disabled': 'Account is disabled',

    // Account Page
    'account.title': 'Account Settings',
    'account.email': 'Email',
    'account.current_password': 'Current Password',
    'account.new_password': 'New Password',
    'account.confirm_password': 'Confirm Password',
    'account.full_name': 'Full Name',
    'account.linked_accounts': 'Linked Accounts',
    'account.add_paywall': 'Add a paywall site',
    'account.select_site': 'Select a site...',
    'account.email_or_member': 'Email or Member Number',
    'account.add_account': 'Add account',
    'account.saving': 'Saving…',
    'account.loading': 'Loading…',
    'account.account_exists': 'An account for "',
    'account.already_exists': '" already exists.',
    'account.encryption_error': 'Encryption key not configured on the server.',
    'account.save_failed': 'Failed to save account. Please try again.',
    'account.remove': 'Remove',
    'account.deleting': 'Deleting…',
    'account.user': 'User',
    
    // Input Modal
    'input.title': 'Input Required',
    'input.submit': 'Submit Response',
    
    // Error Messages
    'error.network': 'Network error. Please check your connection.',
    'error.auth': 'Authentication failed. Please login again.',
    'error.server': 'Server error. Please try again later.',
    'error.retry': 'Retry',
    
    // Connection Status
    'connection.connected': 'Connected',
    'connection.disconnected': 'Disconnected',
    
    // Thinking Display
    
    // Project Manager
    'manager.title': 'AI4Med',
    'manager.search': 'Search projects...',
    'manager.status_all': 'All statuses',
    'manager.new_project': 'New Project',
    'manager.creating': 'Creating...',
    'manager.project_name_label': 'Project Name *',
    'manager.project_name_placeholder': 'e.g., Breast Cancer Drug Response Study',
    'manager.chars': 'chars',
    'manager.research_query_label': 'Research Query *',
    'manager.query_placeholder': 'e.g., "breast cancer"[Title] AND humans[mh]',
    'manager.query_desc': 'Describe the research topic you want to explore',
    'manager.max_papers_label': 'Max Papers to Find',
    'manager.limit_note': 'Limit: 1-5000 papers (>200 may take hours; SciHub recommended for high targets)',
    // publication date range filter
    'manager.date_range_label': 'Publication Date Range (optional)',
    'manager.date_start_label': 'Start date',
    'manager.date_end_label': 'End date',
    'manager.date_range_hint': 'Leave empty to use defaults',
    'manager.date_conflict_warning': 'Detected `[dp]` syntax in your query. It conflicts with the date range picker — clear the picker to use the inline syntax, or remove `[dp]` to use the picker.',
    // PubMed advanced syntax help
    'manager.pubmed_syntax_title': 'PubMed Search Syntax',
    'manager.pubmed_syntax_fields': 'Field tags: [Title], [MeSH Terms], [Author], [dp] (date)',
    'manager.pubmed_syntax_boolean': 'Boolean: AND / OR / NOT',
    'manager.pubmed_syntax_phrase': 'Exact phrase: "breast cancer"',
    'manager.pubmed_syntax_examples_label': 'Examples',
    'manager.pubmed_syntax_example_1': '"breast cancer"[Title] AND humans[mh]',
    'manager.pubmed_syntax_example_2': 'glioma AND Smith[Author] AND 2020:2024[dp]',
    // Advanced options + force re-analyze
    'manager.advanced_options_label': 'Advanced Options',
    'manager.force_reanalyze_label': 'Force re-analyze all papers',
    'manager.force_reanalyze_desc': 'Bypass the global analysis cache and re-run the LLM for every paper. Useful when the prompt template was recently bumped. Note: this increases LLM cost.',
    // MeSH expansion toggle.
    'manager.mesh_expansion_label': 'Expand query with MeSH synonyms',
    'manager.mesh_expansion_desc': 'Automatically expand natural-language terms with NCBI MeSH synonyms for higher recall. Skipped when the query already uses PubMed field tags ([Title], [MeSH Terms], etc.) or uppercase boolean operators (AND/OR/NOT).',
    'manager.analysis_model_label': 'Analysis Model',
    'manager.download_model_label': 'Download Model',
    'manager.model_default_option': 'Use default ({{model}})',
    'manager.model_default_option_unknown': 'Use default (env var)',
    'version.badge_tooltip': 'Build: {{build}} · Commit: {{commit}}',
    'manager.cancel': 'Cancel',
    'manager.create_project': 'Create Project',
    'manager.refresh_title': 'Refresh projects list and statistics',
    'manager.page_prev': 'Previous',
    'manager.page_next': 'Next',
    'manager.showing_range': 'Showing {{from}}–{{to}} of {{total}}',
    
    // Login Page (additional keys)
    'login.brand': 'AI4Med',
    'login.welcome_back': 'Welcome back',
    'login.welcome_subtitle': 'Sign in to your AI4Med account',
    'login.email_label': 'Email Address',
    'login.email_placeholder': 'Enter your email',
    'login.password_label': 'Password',
    'login.password_placeholder': 'Enter your password',
    'login.forgot_password': 'Forgot password?',
    'login.show_password': 'Show password',
    'login.hide_password': 'Hide password',
    'login.signing_in': 'Signing in...',
    'login.login_btn': 'Login',
    'login.ai_powered_title': 'Automatically Discover Cancer Drug Response Datasets with AI',
    'login.ai_powered_desc': 'Automate discovery, analysis, and acquisition of cancer drug response datasets from research papers.',
    'login.find_title': 'Find',
    'login.find_desc': 'Search literature and download papers',
    'login.analyze_title': 'Analyze',
    'login.analyze_desc': 'Extract accession IDs and classify data',
    'login.download_title': 'Download',
    'login.download_desc': 'Automated dataset download',
    'login.contact_admin': 'Contact administrator (liupengfei@intelliflux.cc) to create an account',
    
    // Forgot Password Page
    'forgot.title': 'Reset Password',
    'forgot.subtitle': 'Enter your email address and we will send you a link to reset your password',
    'forgot.email_label': 'Email Address',
    'forgot.email_placeholder': 'Enter your email',
    'forgot.submit': 'Send Reset Link',
    'forgot.sending': 'Sending...',
    'forgot.success': 'If an account with that email exists, a password reset link has been sent',
    'forgot.back_login': 'Back to Login',
    
    // Reset Password Page
    'reset.title': 'Set New Password',
    'reset.new_password_label': 'New Password',
    'reset.new_password_placeholder': 'At least 8 characters',
    'reset.confirm_password_label': 'Confirm Password',
    'reset.confirm_password_placeholder': 'Confirm your password',
    'reset.submit': 'Reset Password',
    'reset.success': 'Password reset successfully! Redirecting to login...',
    'reset.error': 'Invalid or expired reset token',
    'reset.password_mismatch': 'Passwords do not match',
    
    // Admin Page
    'admin.title': 'User Management',
    'admin.add_user': 'Add User',
    'admin.name': 'Name',
    'admin.email': 'Email',
    'admin.role': 'Role',
    'admin.status': 'Status',
    'admin.created': 'Created',
    'admin.actions': 'Actions',
    'admin.admin': 'Admin',
    'admin.user': 'User',
    'admin.active': 'Active',
    'admin.inactive': 'Inactive',
    'admin.delete': 'Delete',
    'admin.delete_confirm': 'Are you sure you want to delete this user?',
    'admin.create_title': 'Create New User',
    'admin.create_name': 'Name',
    'admin.create_name_placeholder': 'Enter user name',
    'admin.create_email': 'Email',
    'admin.create_email_placeholder': 'Enter user email',
    'admin.create_password': 'Password',
    'admin.create_password_placeholder': 'At least 8 characters',
    'admin.create_role': 'Role',
    'admin.create_submit': 'Create User',
    'admin.creating': 'Creating...',
    'admin.success': 'User created successfully',
    'admin.update_success': 'User updated successfully',
    'admin.delete_success': 'User deleted successfully',
    'admin.reset_success': 'Password reset successfully',
    'admin.error': 'Operation failed',
    'admin.cannot_delete_admin': 'Cannot delete admin account',
    'admin.edit': 'Edit',
    'admin.edit_title': 'Edit User',
    'admin.save': 'Save',
    'admin.reset_password': 'Reset password',
    'admin.reset_for': 'Reset password for',
    'admin.reset_submit': 'Reset password',
    'admin.you': 'You',
    'admin.delete_self_blocked': 'You cannot delete your own account',
    'admin.no_users': 'No users match the current filter',
    'admin.search_placeholder': 'Search by name or email',
    'admin.password_hint': 'Minimum 8 characters',
    'admin.show_password': 'Show password',
    'admin.hide_password': 'Hide password',
    'admin.error_name_required': 'Name is required',
    'admin.error_email_invalid': 'Invalid email format',
    'admin.error_password_short': 'Password must be at least 8 characters',

    // Account Page (additional)
    'account.change_password': 'Change Password',
    'account.current_password_placeholder': 'Enter current password',
    'account.new_password_placeholder': 'Enter new password',
    'account.confirm_password_placeholder': 'Confirm new password',
    'account.change_password_btn': 'Change Password',
    'account.changing': 'Changing...',
    'account.password_changed': 'Password changed successfully',
    'account.password_error': 'Failed to change password',
    'account.password_mismatch': 'Passwords do not match',
    'account.wrong_current_password': 'Current password is incorrect',
    
    // Validation Errors
    'error.name_required': 'Name is required',
    'error.password_length': 'Password must be at least 8 characters',
    'error.login_failed': 'Login failed',
    'error.project_name_invalid': 'Project name must be between 3 and 100 characters',
    'error.query_invalid': 'Query must be between 10 and 2000 characters',
    // date range validation error
    'error.date_range_invalid': 'Start date must be earlier than or equal to end date',

    // Review Queue page
    'review_queue.title': 'Review Queue (Tier 2/3 Papers)',
    'review_queue.refresh': 'Refresh',
    'review_queue.preview_markdown': 'View analysis markdown',
    'review_queue.open_project': 'Open project',
    'review_queue.filter_status': 'Status',
    'review_queue.total_items': 'items',
    'review_queue.empty_state': 'Nothing to review right now.',
    'review_queue.column_paper': 'Paper',
    'review_queue.column_project': 'Project',
    'review_queue.column_classification': 'Classification',
    'review_queue.column_status': 'Status',
    'review_queue.column_actions': 'Actions',
    'review_queue.status_pending': 'Pending',
    'review_queue.status_processing': 'Processing',
    'review_queue.status_handled': 'Handled',
    'review_queue.status_skipped': 'Skipped',
    'review_queue.flag_contact_author': 'Author contact suggested',
    'review_queue.flag_manual_required': 'Manual review suggested',
    'review_queue.action_resume_download': 'Resume download',
    'review_queue.action_in_progress': 'I\'ll handle it',
    'review_queue.action_skip': 'Skip',
    'review_queue.action_mark_handled': 'Mark done',
    'review_queue.action_return_to_pending': 'Back to pending',
    'review_queue.prev_page': '← Prev',
    'review_queue.next_page': 'Next →',
    // Inline guidance panel (collapsible).
    'review_queue.info_toggle_open': 'How this works',
    'review_queue.info_toggle_close': 'Hide help',
    'review_queue.info_intro': 'Papers listed here need a human decision before the pipeline can continue with them.',
    'review_queue.info_section_flags': 'Why a paper appears here',
    'review_queue.info_flag_contact_author': 'The analysis suggests the dataset lives in supplementary materials or only with the corresponding author.',
    'review_queue.info_flag_manual_required': 'The pipeline could not auto-decide how to retrieve the dataset and asks you to assess it.',
    'review_queue.info_section_actions': 'What each button does',
    'review_queue.info_action_resume': 'Re-runs the download stage for this paper using its cached analysis plan. Only shown when the previous download genuinely failed (no completed/partial/awaiting_external artifact) — a paper that already produced some dataset row is hidden because re-running yields the same outcome.',
    'review_queue.info_action_in_progress': 'Marks the paper as being handled by you (e.g. you are about to email the author or fetch the data manually). It moves out of the pending list until you mark it done.',
    'review_queue.info_action_skip': 'Drop this paper for good. It will not show up again in any status filter.',
    'review_queue.info_action_handled': 'You are finished with this paper (data obtained or confirmed unavailable). Removes it from active work.',
    'review_queue.info_action_return': 'Changed your mind — send a paper that you took into Processing back to the Pending pile.',
    
    // Artifact List (additional keys)
    'artifacts.filter.all': 'All',
    'artifacts.filter.has_analysis': 'Has Analysis',
    'artifacts.filter.has_datasets': 'Has Datasets',
    'artifacts.sort.asc': 'Sort Ascending',
    'artifacts.sort.desc': 'Sort Descending',
    'artifacts.both_data': 'Both Data',
    'artifacts.seq_only': 'Sequencing Only',
    'artifacts.drug_only': 'Drug Only',
    'artifacts.manual': 'Manual',
    'artifacts.contact': 'Contact',
    'artifacts.both_tooltip': 'Has both sequencing and drug data',
    'artifacts.seq_tooltip': 'Only sequencing data available',
    'artifacts.drug_tooltip': 'Only drug response data available',
    'artifacts.manual_tooltip': 'Data available but requires manual extraction',
    'artifacts.contact_tooltip': 'Contact author or no suitable data',
    
    // Email Modal
    'email.error_no_recipients': 'Please enter at least one recipient email address',
    'email.send_failed': 'Failed to send email',
    
    // File Viewer
    
    // Message Log
    'messages.stage.all': 'All',
    
    // Input Modal (additional keys)
    'input.from': 'From',
    'input.requested': 'Requested',
    'input.response_label': 'Your Response',
    'input.placeholder': 'Enter your response...',
    'input.hint': 'Use Ctrl+Enter (or Cmd+Enter) to submit quickly',
    'input.submitting': 'Submitting...',
    'input.cancel': 'Cancel',
    
    // Email Modal (additional keys)
    'email.new_message': 'New Message',
    'email.sent_success': 'Email sent successfully!',
    'email.to_label': 'To',
    'email.subject_label': 'Subject',
    'email.compose_placeholder': 'Compose message...',
    'email.recipients_placeholder': 'Recipients',
    'email.sending': 'Sending...',
    'email.send_button': 'Send Email',
    'email.discard_title': 'Discard',
    'email.drafting': 'Drafting...',
    
    // Project Grid
    'grid.no_projects': 'No projects found',
    'grid.create_first': 'Create your first research project to get started',
    'grid.project': 'Project',
    'grid.find_papers': 'Find',
    'grid.analyze': 'Analyze',
    'grid.download': 'Download',
    'grid.qualify': 'Qualify',
    'grid.pause': 'Pause',
    'grid.resume': 'Resume',
    'grid.delete': 'Delete',
    'grid.pause_title': 'Pause Project',
    'grid.resume_title': 'Resume Project',
    'grid.delete_title': 'Delete Project',
    'grid.pause_confirm': 'Are you sure you want to pause',
    'grid.resume_confirm': 'Are you sure you want to resume',
    'grid.delete_confirm': 'Are you sure you want to delete "',
    'grid.delete_confirm_suffix': '"? This will stop the pipeline and remove the project from the main view.',
    'grid.loading': 'Loading...',
    
    // Artifacts List (additional keys)
    'artifacts.files': 'Files',
    'artifacts.file': 'File',
    'artifacts.date': 'Date',
    'artifacts.status': 'Status',
    'artifacts.paper': 'Paper',
    'artifacts.data_type': 'Download Outcome',
    'artifacts.analysis_status': 'Analysis Status',
    'artifacts.qualification': 'Qualification',
    'artifacts.qual_strict': 'Strict',
    'artifacts.qual_loose': 'Loose',
    'artifacts.qual_fail': 'Fail',
    'artifacts.qual_error': 'Error',
    'artifacts.qual_processing': 'Processing',
    'artifacts.qual_pending': 'Pending',
    'artifacts.qual_none': '—',
    'artifacts.cost': 'Cost',
    'artifacts.total_cost': 'Total cost',
    'artifacts.cost_prescreen': 'Prescreen',
    'artifacts.cost_analyze': 'Analyze',
    'artifacts.cost_download': 'Download',
    'artifacts.cost_qualify': 'Qualify',
    'artifacts.cost_tokens': 'Total tokens',
    'artifacts.analysis': 'Analysis',
    'artifacts.no_analysis': 'No analysis',
    'artifacts.datasets': 'Datasets',
    'artifacts.embedded_datasets': 'Embedded Data',
    'artifacts.evidence': 'Evidence',
    'artifacts.evidence_only': 'Evidence only',
    'artifacts.no_downloadable_datasets': 'No downloadable dataset has been produced yet. Review the evidence to continue extraction.',
    'artifacts.selected': 'selected',
    'artifacts.none_for_project': 'No artifacts found for this project',
    'artifacts.search_placeholder': 'Search papers (e.g., Kumar2014)...',
    'artifacts.no_matches': 'No artifacts match your filters',
    'artifacts.repository_dataset_tooltip': 'At least one valid dataset file was recorded',
    'artifacts.embedded_dataset_tooltip': 'Structured data was extracted from paper tables or figures',
    'artifacts.acquisition_evidence_tooltip': 'Only acquisition evidence was recorded',
    'artifacts.controlled_access_tooltip': 'Dataset access requires authentication or approval',
    'artifacts.browser_blocked_tooltip': 'Publisher or repository browser verification blocked acquisition',
    'artifacts.accession_missing_tooltip': 'A stable repository accession could not be identified',
    'artifacts.pdf_only_tooltip': 'Only PDF material was available; no structured dataset was recorded',
    'artifacts.no_valid_dataset_tooltip': 'Downloaded files were empty, invalid, duplicate, or not dataset files',
    'artifacts.adjust_filters': 'Try adjusting your search or filters',
    
    // File Viewer Modal
    'file.no_content': 'No content available for this file',
    'file.pdf_old_format': 'This PDF was stored with the old format.',
    'file.pdf_migration_hint': 'Please re-run the migration script to update it.',
    'file.loading': 'Loading file content...',
    'file.load_failed': 'Failed to load file content',
    'file.close_btn': 'Close',
    
    // Error Boundary
    'error.boundary.message': 'The application encountered an unexpected error',
    'error.boundary.details': 'Error Details',
    'error.boundary.stack': 'Component Stack',
    'error.boundary.try_again': 'Try Again',
    'error.boundary.reload': 'Reload Page',
    'error.boundary.hint': 'If this error persists, please check the browser console for more details or restart the backend server.',
    
    // Project Detail (additional keys)
    'detail.loading': 'Loading project details...',
    'detail.failed_load': 'Failed to Load Project',
    'detail.not_found': 'Project not found',
    'detail.back_home': 'Back to Home',
    'detail.try_again': 'Try Again',
    'detail.overview': 'Overview',
    'detail.stages': 'Stages',
    'detail.artifacts': 'Artifacts',
    'detail.messages': 'Messages',
    'detail.created': 'Created',
    'detail.updated': 'Updated',
    'detail.duration': 'Duration',
    'detail.project_overview': 'Project Overview',
    'detail.project_id': 'Project ID',
    'detail.status': 'Status',
    'detail.stages_completed': 'Stages Completed',
    'detail.total_artifacts': 'Total Artifacts',
    'detail.quick_stats': 'Quick Stats',
    'detail.papers': 'Papers',
    'detail.analyses': 'Analyses',
    'detail.datasets': 'Datasets',
    'detail.embedded_datasets': 'Embedded Data',
    'detail.acquisition_evidence': 'Acquisition Evidence',
    'detail.awaiting_reply': 'Awaiting reply',
    'detail.find_summary_title': 'Find Stage Summary',
    'detail.find_summary_total_candidates': 'Candidates found',
    'detail.find_summary_cross_skipped': 'Skipped (in other projects)',
    'detail.find_summary_downloaded': 'Successfully downloaded',
    'detail.find_summary_shortfall': 'Shortfall vs target',
    'detail.find_summary_requested': 'Requested target',
    'detail.find_summary_resumed': 'Resumed from this project',
    'detail.find_summary_no_data': 'Find stage has not produced a summary yet.',
    'detail.data_types_summary': 'Papers by Download Outcome',
    'detail.contact_author': 'Contact Author',
    'detail.sent': 'Sent',
    'detail.email_again': 'Email Again',
    'detail.email': 'Email',
    'detail.no_data_types': 'No paper download outcomes available yet',
    'detail.no_papers_require_contact': 'No papers require author contact',
    'detail.pipeline_stages': 'Stages',
    'detail.project_artifacts': 'Project Artifacts',
    'detail.download_logs': 'Logs',
    // Review tab label
    'detail.review': 'Review',
    // Overview CTA banner — pending review count
    'detail.review_cta_pending': 'You have {{count}} paper(s) awaiting review',
    // NotFoundPage strings (catch-all route)
    'not_found.title': 'Page not found',
    'not_found.message': "The page you're looking for doesn't exist.",
    'not_found.back_home': 'Back to home',
    'detail.download_detailed_logs': 'Logs',
    'detail.general_download_logs': 'General',
    'detail.no_download_logs': 'No execution logs available for this project.',
    'detail.download_debug_screenshot': 'download debug screenshot',
    'detail.download_debug_screenshot_full': 'download debug screenshot full',
    'detail.pipeline': 'Pipeline',
    'detail.analysis_model': 'Analysis Model',
    'detail.download_model': 'Download Model',
    'detail.model_default': 'Default (env var)',
    'detail.model_default_with_value': 'Default ({{model}})',
    'detail.model_default_hint': 'Current env-resolved default. May differ from the model the project actually ran with.',

    // Stage Labels
    'stage.in_progress': 'In Progress',
    'stage.current': 'Current',
    'stage.started': 'Started',
    'stage.completed': 'Completed',
    'stage.not_started': 'Not started',
    'stage.ended': 'Ended',
    'stage.error': 'Error',
    'stage.output': 'Output',
    'stage.active': 'Active',
    'stage.running': 'Running',
    'stage.pending': 'Pending',
    'stage.paused': 'Paused',
    'stage.cancelled': 'Cancelled',
    
    // Pipeline Stages (Names and Descriptions)
    'pipeline.find': 'Find',
    'pipeline.analyze': 'Analyze',
    'pipeline.download': 'Download',
    'pipeline.qualify': 'Qualify',
    'pipeline.complete': 'Complete',
    'pipeline.find_desc': 'Searching and downloading research papers',
    'pipeline.analyze_desc': 'Analyzing papers for data suitability',
    'pipeline.download_desc': 'Downloading datasets',
    'pipeline.qualify_desc': 'Qualifying downloaded datasets',
    'pipeline.complete_desc': 'All stages completed',
  },
  
  zh: {
    // Common
    'common.loading': '加载中...',
    'common.error': '错误',
    'common.cancel': '取消',
    'common.delete': '删除',
    'common.back': '返回',
    
    // Status
    'status.pending': '等待中',
    'status.running': '运行中',
    'status.completed': '已完成',
    'status.failed': '失败',
    'status.cancelled': '已取消',
    'status.paused': '已暂停',
    'status.error': '错误',
    
    // Pipeline Stages
    'stage.find': '查找',
    'stage.analyze': '分析',
    'stage.download': '下载',
    'stage.qualify': '验证',
    'stage.complete': '完成',
    
    // Data Classification
    'data.both': '两种数据类型',
    'data.sequencing_only': '仅测序数据',
    'data.drug_only': '仅药物反应数据',
    'data.manual': '需要手动操作',
    'data.contact': '联系',
    'data.repository_dataset': 'DB 数据集',
    'data.embedded_dataset': '表格数据',
    'data.acquisition_evidence': '证据',
    'data.controlled_access': '受控',
    'data.browser_blocked': '阻断',
    'data.accession_missing': '缺编号',
    'data.pdf_only': '仅 PDF',
    'data.no_valid_dataset': '无有效数据',
    'data.explain.repository_dataset': '已下载可用文件',
    'data.explain.embedded_dataset': '表格/图抽取',
    'data.explain.acquisition_evidence': '仅保留证据',
    'data.explain.controlled_access': '需审批访问',
    'data.explain.browser_blocked': '站点验证阻断',
    'data.explain.accession_missing': '缺少稳定编号',
    'data.explain.pdf_only': '仅 PDF，无表格文件',
    'data.explain.no_valid_dataset': '文件无效或为空',
    'data.explain.contact_author': '需作者回复',
    'data.explain.both_data': '测序和药物数据',
    'data.explain.sequencing_only': '仅测序数据',
    'data.explain.drug_only': '仅药物反应',
    'data.explain.manual_required': '需手动抽取',
    
    // Home Page
    'home.title': 'AI4Med',
    'home.projects': '项目',
    
    // Project Card
    'project.view': '查看详情',
    
    // Project Detail
    
    // Stage Card
    'stage.status': '状态',
    'stage.progress': '进度',
    'stage.start_time': '开始时间',
    'stage.end_time': '结束时间',
    'stage.duration': '持续时间',
    'stage.messages': '消息',
    'stage.quick_stats': '快速统计',
    'stage.artifacts': '产物数',
    'stage.no_messages': '暂无消息',

    // Relative time & duration (shared utils formatters)
    'time.relative.just_now': '刚刚',
    'time.relative.minutes_ago': '{{n}}分钟前',
    'time.relative.hours_ago': '{{n}}小时前',
    'time.relative.days_ago': '{{n}}天前',
    'time.duration.seconds': '{{n}}秒',
    'time.duration.less_than_second': '不到1秒',
    'time.duration.min_sec': '{{m}}分{{s}}秒',
    'time.duration.hr_min': '{{h}}小时{{m}}分',
    'time.placeholder.dash': '-',
    
    // Artifacts
    'artifacts.name': '名称',
    'artifacts.download': '下载',
    'detail.show_all_screenshots': '展开全部截图（{{count}}）',
    'detail.hide_screenshots': '收起截图',
    
    // Messages
    'messages.no_messages': '暂无消息',
    
    // User Menu
    'user.account': '账户',
    'user.language': '语言',
    'user.logout': '退出登录',

    // Languages
    
    // Login Page
    'login.error': '邮箱或密码无效',
    'login.error_disabled': '账户已被禁用',

    // Account Page
    'account.title': '账户设置',
    'account.email': '邮箱',
    'account.current_password': '当前密码',
    'account.new_password': '新密码',
    'account.confirm_password': '确认密码',
    'account.full_name': '全名',
    'account.linked_accounts': '关联账户',
    'account.add_paywall': '添加付费网站账户',
    'account.select_site': '选择网站...',
    'account.email_or_member': '邮箱或会员编号',
    'account.add_account': '添加账户',
    'account.saving': '保存中…',
    'account.loading': '加载中…',
    'account.account_exists': '该网站已存在账户："',
    'account.already_exists': '"',
    'account.encryption_error': '服务器未配置加密密钥。',
    'account.save_failed': '保存失败，请重试。',
    'account.remove': '移除',
    'account.deleting': '删除中…',
    'account.user': '用户',
    
    // Input Modal
    'input.title': '需要输入',
    'input.submit': '提交回复',
    
    // Error Messages
    'error.network': '网络错误。请检查您的连接。',
    'error.auth': '认证失败。请重新登录。',
    'error.server': '服务器错误。请稍后重试。',
    'error.retry': '重试',
    
    // Connection Status
    'connection.connected': '已连接',
    'connection.disconnected': '已断开',
    
    // Thinking Display
    
    // Project Manager
    'manager.title': 'AI4Med',
    'manager.search': '搜索项目...',
    'manager.status_all': '全部状态',
    'manager.new_project': '新项目',
    'manager.creating': '创建中...',
    'manager.project_name_label': '项目名称 *',
    'manager.project_name_placeholder': 'e.g., Breast Cancer Drug Response Study',
    'manager.chars': '个字符',
    'manager.research_query_label': '研究查询 *',
    'manager.query_placeholder': '例如："breast cancer"[Title] AND humans[mh]',
    'manager.query_desc': '描述您想要探索的研究主题',
    'manager.max_papers_label': '最大查找论文数',
    'manager.limit_note': '限制：1-5000 篇论文（>200 篇可能需数小时；高目标建议开启 SciHub 兜底）',
    // publication date range filter
    'manager.date_range_label': '发表日期范围（可选）',
    'manager.date_start_label': '起始日期',
    'manager.date_end_label': '截止日期',
    'manager.date_range_hint': '留空则使用默认值',
    'manager.date_conflict_warning': '检测到查询中包含 `[dp]` 语法。它与日期范围选择器冲突——请清空选择器以使用搜索框中的日期语法，或删除 `[dp]` 以使用选择器。',
    // PubMed advanced syntax help
    'manager.pubmed_syntax_title': 'PubMed 搜索语法',
    'manager.pubmed_syntax_fields': '字段标签：[Title]、[MeSH Terms]、[Author]、[dp]（日期）',
    'manager.pubmed_syntax_boolean': '布尔：AND / OR / NOT',
    'manager.pubmed_syntax_phrase': '精确短语："breast cancer"',
    'manager.pubmed_syntax_examples_label': '示例',
    'manager.pubmed_syntax_example_1': '"breast cancer"[Title] AND humans[mh]',
    'manager.pubmed_syntax_example_2': 'glioma AND Smith[Author] AND 2020:2024[dp]',
    // Advanced options + force re-analyze
    'manager.advanced_options_label': '高级选项',
    'manager.force_reanalyze_label': '强制重新分析所有论文',
    'manager.force_reanalyze_desc': '跳过全局分析缓存，对每篇论文重新调 LLM。在 prompt 模板刚升级后想验证新结果时使用。注意：会增加 LLM 成本。',
    // MeSH 扩展开关
    'manager.mesh_expansion_label': '用 MeSH 同义词扩展查询',
    'manager.mesh_expansion_desc': '自动用 NCBI MeSH 同义词扩展自然语言术语以提升召回率。当查询已使用 PubMed 字段标签（[Title]、[MeSH Terms] 等）或大写布尔运算符（AND/OR/NOT）时自动跳过。',
    'manager.analysis_model_label': '分析模型',
    'manager.download_model_label': '下载模型',
    'manager.model_default_option': '使用默认值（{{model}}）',
    'manager.model_default_option_unknown': '使用默认值（环境变量）',
    'version.badge_tooltip': '构建时间：{{build}} · 提交：{{commit}}',
    'manager.cancel': '取消',
    'manager.create_project': '创建项目',
    'manager.refresh_title': '刷新项目列表与统计',
    'manager.page_prev': '上一页',
    'manager.page_next': '下一页',
    'manager.showing_range': '第 {{from}}-{{to}} 项 / 共 {{total}} 项',
    
    // Login Page (additional keys)
    'login.brand': 'AI4Med',
    'login.welcome_back': '欢迎回来',
    'login.welcome_subtitle': '登录您的 AI4Med 账户',
    'login.email_label': '邮箱地址',
    'login.email_placeholder': '输入您的邮箱',
    'login.password_label': '密码',
    'login.password_placeholder': '请输入密码',
    'login.forgot_password': '忘记密码？',
    'login.show_password': '显示密码',
    'login.hide_password': '隐藏密码',
    'login.signing_in': '正在登录...',
    'login.login_btn': '登录',
    'login.ai_powered_title': 'AI 自动发现癌症药物反应数据集',
    'login.ai_powered_desc': '自动化从研究论文中发现、分析和获取癌症药物反应数据集。',
    'login.find_title': '查找',
    'login.find_desc': '搜索文献并下载论文',
    'login.analyze_title': '分析',
    'login.analyze_desc': '提取数据集编号并分类',
    'login.download_title': '下载',
    'login.download_desc': '自动下载数据集',
    'login.contact_admin': '请联系管理员（liupengfei@intelliflux.cc）创建账户',
    
    // Forgot Password Page
    'forgot.title': '重置密码',
    'forgot.subtitle': '输入您的邮箱地址，我们将发送密码重置链接',
    'forgot.email_label': '邮箱地址',
    'forgot.email_placeholder': '输入您的邮箱',
    'forgot.submit': '发送重置链接',
    'forgot.sending': '发送中...',
    'forgot.success': '如果该邮箱存在账户，我们将发送密码重置链接',
    'forgot.back_login': '返回登录',
    
    // Reset Password Page
    'reset.title': '设置新密码',
    'reset.new_password_label': '新密码',
    'reset.new_password_placeholder': '最少 8 个字符',
    'reset.confirm_password_label': '确认密码',
    'reset.confirm_password_placeholder': '确认您的密码',
    'reset.submit': '重置密码',
    'reset.success': '密码重置成功！正在跳转到登录页面...',
    'reset.error': '无效或已过期的重置令牌',
    'reset.password_mismatch': '两次输入的密码不一致',
    
    // Admin Page
    'admin.title': '用户管理',
    'admin.add_user': '添加用户',
    'admin.name': '姓名',
    'admin.email': '邮箱',
    'admin.role': '角色',
    'admin.status': '状态',
    'admin.created': '创建时间',
    'admin.actions': '操作',
    'admin.admin': '管理员',
    'admin.user': '普通用户',
    'admin.active': '活跃',
    'admin.inactive': '已禁用',
    'admin.delete': '删除',
    'admin.delete_confirm': '确定要删除此用户吗？',
    'admin.create_title': '创建新用户',
    'admin.create_name': '姓名',
    'admin.create_name_placeholder': '输入用户姓名',
    'admin.create_email': '邮箱',
    'admin.create_email_placeholder': '输入用户邮箱',
    'admin.create_password': '密码',
    'admin.create_password_placeholder': '最少 8 个字符',
    'admin.create_role': '角色',
    'admin.create_submit': '创建用户',
    'admin.creating': '创建中...',
    'admin.success': '用户创建成功',
    'admin.update_success': '用户已更新',
    'admin.delete_success': '用户删除成功',
    'admin.reset_success': '密码重置成功',
    'admin.error': '操作失败',
    'admin.cannot_delete_admin': '无法删除管理员账户',
    'admin.edit': '编辑',
    'admin.edit_title': '编辑用户',
    'admin.save': '保存',
    'admin.reset_password': '重置密码',
    'admin.reset_for': '为以下账号重置密码',
    'admin.reset_submit': '重置密码',
    'admin.you': '本人',
    'admin.delete_self_blocked': '不能删除自己的账号',
    'admin.no_users': '没有匹配的用户',
    'admin.search_placeholder': '按姓名或邮箱搜索',
    'admin.password_hint': '至少 8 个字符',
    'admin.show_password': '显示密码',
    'admin.hide_password': '隐藏密码',
    'admin.error_name_required': '请填写姓名',
    'admin.error_email_invalid': '邮箱格式无效',
    'admin.error_password_short': '密码至少 8 个字符',
    
    // Account Page (additional)
    'account.change_password': '修改密码',
    'account.current_password_placeholder': '输入当前密码',
    'account.new_password_placeholder': '输入新密码',
    'account.confirm_password_placeholder': '确认新密码',
    'account.change_password_btn': '修改密码',
    'account.changing': '修改中...',
    'account.password_changed': '密码修改成功',
    'account.password_error': '密码修改失败',
    'account.password_mismatch': '两次输入的密码不一致',
    'account.wrong_current_password': '当前密码错误',
    
    // Validation Errors
    'error.name_required': '姓名为必填项',
    'error.password_length': '密码必须至少 8 个字符',
    'error.login_failed': '登录失败',
    'error.project_name_invalid': '项目名称必须在 3 到 100 个字符之间',
    'error.query_invalid': '查询必须在 10 到 2000 个字符之间',
    // date range validation error
    'error.date_range_invalid': '起始日期必须早于或等于截止日期',

    // Review Queue 页面
    'review_queue.title': '待审核队列（Tier 2/3 论文）',
    'review_queue.refresh': '刷新',
    'review_queue.preview_markdown': '查看分析 markdown',
    'review_queue.open_project': '打开项目',
    'review_queue.filter_status': '状态',
    'review_queue.total_items': '条',
    'review_queue.empty_state': '当前没有待审核的论文。',
    'review_queue.column_paper': '论文',
    'review_queue.column_project': '项目',
    'review_queue.column_classification': '分类',
    'review_queue.column_status': '状态',
    'review_queue.column_actions': '操作',
    'review_queue.status_pending': '待处理',
    'review_queue.status_processing': '处理中',
    'review_queue.status_handled': '已处理',
    'review_queue.status_skipped': '已跳过',
    'review_queue.flag_contact_author': '建议联系作者',
    'review_queue.flag_manual_required': '建议人工评估',
    'review_queue.action_resume_download': '续跑下载',
    'review_queue.action_in_progress': '我来跟进',
    'review_queue.action_skip': '跳过',
    'review_queue.action_mark_handled': '标为完成',
    'review_queue.action_return_to_pending': '退回待处理',
    'review_queue.prev_page': '← 上一页',
    'review_queue.next_page': '下一页 →',
    // 顶部说明面板（可折叠）
    'review_queue.info_toggle_open': '这个页面怎么用',
    'review_queue.info_toggle_close': '收起说明',
    'review_queue.info_intro': '这里列出的论文需要人工判断后流程才能继续。',
    'review_queue.info_section_flags': '为什么论文出现在这里',
    'review_queue.info_flag_contact_author': '分析显示数据可能在补充材料中或需向通讯作者索取。',
    'review_queue.info_flag_manual_required': '流程无法自动判断数据获取路径，需要你评估。',
    'review_queue.info_section_actions': '每个按钮的作用',
    'review_queue.info_action_resume': '用上次分析阶段保存的 plan 重跑下载。只有当上次下载真的全失败（无 completed/partial/awaiting_external 产物）时才显示——已经有 partial 产物的 paper 会自动隐藏此按钮，因为重跑也只会得到一样的结果。',
    'review_queue.info_action_in_progress': '标记为"由我跟进"（比如你打算去发邮件给作者 / 自己手动找数据）。它会从待处理列表里挪走，直到你点"标为完成"。',
    'review_queue.info_action_skip': '彻底放弃这一篇。不会再出现在任何状态过滤下。',
    'review_queue.info_action_handled': '你已经处理完这一篇（拿到数据了 / 确认无法获取），从活跃工作中移除。',
    'review_queue.info_action_return': '反悔——把已经拿去处理的论文退回待处理列表。',
    
    // Artifact List
    'artifacts.filter.all': '全部',
    'artifacts.filter.has_analysis': '有分析',
    'artifacts.filter.has_datasets': '有数据集',
    'artifacts.sort.asc': '升序排序',
    'artifacts.sort.desc': '降序排序',
    'artifacts.both_data': '两种数据',
    'artifacts.seq_only': '仅测序',
    'artifacts.drug_only': '仅药物',
    'artifacts.manual': '手动',
    'artifacts.contact': '联系',
    'artifacts.both_tooltip': '同时具有测序和药物数据',
    'artifacts.seq_tooltip': '仅有测序数据',
    'artifacts.drug_tooltip': '仅有药物数据',
    'artifacts.manual_tooltip': '数据可用但需要手动提取',
    'artifacts.contact_tooltip': '联系作者或无合适数据',
    
    // Email Modal
    'email.error_no_recipients': '请输入至少一个收件人邮箱地址',
    'email.send_failed': '邮件发送失败',
    
    // File Viewer
    
    // Message Log
    'messages.stage.all': '全部',
    
    // Input Modal (additional keys)
    'input.from': '来自',
    'input.requested': '请求时间',
    'input.response_label': '您的回复',
    'input.placeholder': '请输入您的回复...',
    'input.hint': '使用 Ctrl+Enter (或 Cmd+Enter) 快速提交',
    'input.submitting': '提交中...',
    'input.cancel': '取消',
    
    // Email Modal (additional keys)
    'email.new_message': '新消息',
    'email.sent_success': '邮件发送成功！',
    'email.to_label': '收件人',
    'email.subject_label': '主题',
    'email.compose_placeholder': '撰写邮件...',
    'email.recipients_placeholder': '收件人',
    'email.sending': '发送中...',
    'email.send_button': '发送邮件',
    'email.discard_title': '丢弃',
    'email.drafting': '正在生成草稿...',
    
    // Project Grid
    'grid.no_projects': '未找到项目',
    'grid.create_first': '创建您的第一个研究项目以开始',
    'grid.project': '项目',
    'grid.find_papers': '查找',
    'grid.analyze': '分析',
    'grid.download': '下载',
    'grid.qualify': '验证',
    'grid.pause': '暂停',
    'grid.resume': '继续',
    'grid.delete': '删除',
    'grid.pause_title': '暂停项目',
    'grid.resume_title': '继续项目',
    'grid.delete_title': '删除项目',
    'grid.pause_confirm': '确定要暂停',
    'grid.resume_confirm': '确定要继续',
    'grid.delete_confirm': '确定要删除 "',
    'grid.delete_confirm_suffix': '" 吗？这将停止管道并从主视图中移除项目。',
    'grid.loading': '加载中...',
    
    // Artifacts List (additional keys)
    'artifacts.files': '文件',
    'artifacts.file': '文件',
    'artifacts.date': '日期',
    'artifacts.status': '状态',
    'artifacts.paper': '论文',
    'artifacts.data_type': '下载结果',
    'artifacts.analysis_status': '分析状态',
    'artifacts.qualification': '验证结果',
    'artifacts.qual_strict': '严格合格',
    'artifacts.qual_loose': '宽松合格',
    'artifacts.qual_fail': '不合格',
    'artifacts.qual_error': '错误',
    'artifacts.qual_processing': '验证中',
    'artifacts.qual_pending': '待验证',
    'artifacts.qual_none': '—',
    'artifacts.cost': '成本',
    'artifacts.total_cost': '总成本',
    'artifacts.cost_prescreen': '预筛选',
    'artifacts.cost_analyze': '分析',
    'artifacts.cost_download': '下载',
    'artifacts.cost_qualify': '验证',
    'artifacts.cost_tokens': '总 token',
    'artifacts.analysis': '分析',
    'artifacts.no_analysis': '待分析',
    'artifacts.datasets': '数据集',
    'artifacts.embedded_datasets': '论文提取数据',
    'artifacts.evidence': '采集证据',
    'artifacts.evidence_only': '仅采集证据',
    'artifacts.no_downloadable_datasets': '尚未生成可下载数据集。请查看采集证据并继续抽取。',
    'artifacts.selected': '已选',
    'artifacts.none_for_project': '该项目暂无产物',
    'artifacts.repository_dataset_tooltip': '已记录至少一个有效数据集文件',
    'artifacts.embedded_dataset_tooltip': '已从论文表格或图中抽取结构化数据',
    'artifacts.acquisition_evidence_tooltip': '仅记录了采集证据',
    'artifacts.controlled_access_tooltip': '数据集访问需要认证或审批',
    'artifacts.browser_blocked_tooltip': '出版商或仓库的浏览器验证阻断采集',
    'artifacts.accession_missing_tooltip': '未能识别稳定的仓库 accession',
    'artifacts.pdf_only_tooltip': '仅有 PDF 材料，未记录结构化数据集',
    'artifacts.no_valid_dataset_tooltip': '下载文件为空、无效、重复，或不是数据集文件',
    'artifacts.search_placeholder': '搜索论文（例如 Kumar2014）...',
    'artifacts.no_matches': '没有符合筛选条件的产物',
    'artifacts.adjust_filters': '尝试调整搜索或筛选条件',
    
    // File Viewer Modal
    'file.no_content': '该文件暂无内容',
    'file.pdf_old_format': '此 PDF 是以旧格式存储的。',
    'file.pdf_migration_hint': '请重新运行迁移脚本以更新它。',
    'file.loading': '正在加载文件内容...',
    'file.load_failed': '加载文件内容失败',
    'file.close_btn': '关闭',
    
    // Error Boundary
    'error.boundary.message': '应用程序遇到了意外错误',
    'error.boundary.details': '错误详情',
    'error.boundary.stack': '组件堆栈',
    'error.boundary.try_again': '重试',
    'error.boundary.reload': '重新加载页面',
    'error.boundary.hint': '如果错误持续存在，请检查浏览器控制台获取更多详情，或重启后端服务器。',
    
    // Project Detail (additional keys)
    'detail.loading': '正在加载项目详情...',
    'detail.failed_load': '加载项目失败',
    'detail.not_found': '项目未找到',
    'detail.back_home': '返回首页',
    'detail.try_again': '重新尝试',
    'detail.overview': '概览',
    'detail.stages': '阶段',
    'detail.artifacts': '产物',
    'detail.messages': '消息',
    'detail.created': '创建时间',
    'detail.updated': '更新时间',
    'detail.duration': '持续时间',
    'detail.project_overview': '项目概览',
    'detail.project_id': '项目 ID',
    'detail.status': '状态',
    'detail.stages_completed': '已完成阶段',
    'detail.total_artifacts': '总产物数',
    'detail.quick_stats': '快速统计',
    'detail.papers': '论文',
    'detail.analyses': '分析',
    'detail.datasets': '数据集',
    'detail.embedded_datasets': '论文提取数据',
    'detail.find_summary_title': '查找阶段汇总',
    'detail.find_summary_total_candidates': '搜索到候选',
    'detail.find_summary_cross_skipped': '跨项目跳过',
    'detail.find_summary_downloaded': '成功下载',
    'detail.find_summary_shortfall': '未达目标缺口',
    'detail.find_summary_requested': '设定目标',
    'detail.find_summary_resumed': '本项目已下',
    'detail.find_summary_no_data': '查找阶段尚未产生汇总。',
    'detail.acquisition_evidence': '采集证据',
    'detail.awaiting_reply': '等待回复',
    'detail.data_types_summary': '按论文统计下载结果',
    'detail.contact_author': '联系作者',
    'detail.sent': '已发送',
    'detail.email_again': '再次发送邮件',
    'detail.email': '发送邮件',
    'detail.no_data_types': '暂无论文下载结果',
    'detail.no_papers_require_contact': '暂无需要联系作者的论文',
    'detail.pipeline_stages': '阶段',
    'detail.project_artifacts': '项目产物',
    'detail.download_logs': '日志',
    // Review tab label
    'detail.review': '待审核',
    // Overview CTA banner — pending review count
    'detail.review_cta_pending': '你有 {{count}} 篇论文待审核',
    // NotFoundPage strings (catch-all route)
    'not_found.title': '页面不存在',
    'not_found.message': '您要找的页面不存在。',
    'not_found.back_home': '返回首页',
    'detail.download_detailed_logs': '日志',
    'detail.general_download_logs': '通用',
    'detail.no_download_logs': '该项目暂无日志。',
    'detail.download_debug_screenshot': '下载调试截图',
    'detail.download_debug_screenshot_full': '下载调试截图（完整）',
    'detail.pipeline': '流程',
    'detail.analysis_model': '分析模型',
    'detail.download_model': '下载模型',
    'detail.model_default': '默认（环境变量）',
    'detail.model_default_with_value': '默认（{{model}}）',
    'detail.model_default_hint': '当前环境变量解析出的默认模型。可能与项目实际运行时使用的模型不同。',

    // Stage Labels
    'stage.in_progress': '进行中',
    'stage.current': '当前',
    'stage.started': '已开始',
    'stage.completed': '已完成',
    'stage.not_started': '未开始',
    'stage.ended': '已结束',
    'stage.error': '错误',
    'stage.output': '输出',
    'stage.active': '活跃',
    'stage.running': '运行中',
    'stage.pending': '等待中',
    'stage.paused': '已暂停',
    'stage.cancelled': '已取消',
    
    // Pipeline Stages (Names and Descriptions)
    'pipeline.find': '查找',
    'pipeline.analyze': '分析',
    'pipeline.download': '下载',
    'pipeline.qualify': '验证',
    'pipeline.complete': '完成',
    'pipeline.find_desc': '搜索和下载研究论文',
    'pipeline.analyze_desc': '分析论文的数据适用性',
    'pipeline.download_desc': '下载数据集',
    'pipeline.qualify_desc': '验证已下载的数据集',
    'pipeline.complete_desc': '全部阶段已完成',
  },
};

// Current language state
let currentLanguage: Language = 'en';

// Language change listeners
type LanguageChangeListener = (lang: Language) => void;
const listeners: LanguageChangeListener[] = [];

/**
 * Initialize i18n with saved language preference
 */
export function initI18n(): Language {
  const savedLang = localStorage.getItem('user_language');
  
  if (savedLang === 'Chinese') {
    currentLanguage = 'zh';
  } else if (savedLang === 'English') {
    currentLanguage = 'en';
  } else {
    // Detect browser language
    const browserLang = navigator.language.toLowerCase();
    currentLanguage = browserLang.startsWith('zh') ? 'zh' : 'en';
  }
  
  return currentLanguage;
}

/**
 * Get current language
 */
export function getLanguage(): Language {
  return currentLanguage;
}

/**
 * Set language and persist to localStorage
 */
export function setLanguage(lang: Language): void {
  if (currentLanguage !== lang) {
    currentLanguage = lang;
    localStorage.setItem('user_language', lang === 'zh' ? 'Chinese' : 'English');
    notifyListeners();
  }
}

/**
 * Subscribe to language changes
 */
export function onLanguageChange(listener: LanguageChangeListener): () => void {
  listeners.push(listener);
  return () => {
    const index = listeners.indexOf(listener);
    if (index > -1) {
      listeners.splice(index, 1);
    }
  };
}

/**
 * Notify all listeners of language change
 */
function notifyListeners(): void {
  listeners.forEach(listener => listener(currentLanguage));
}

function interpolate(template: string, vars: Record<string, string | number>): string {
  return template.replace(/\{\{(\w+)\}\}/g, (_, name: string) =>
    Object.prototype.hasOwnProperty.call(vars, name) ? String(vars[name]) : ''
  );
}

/**
 * Get translation for a key
 * @param key - Translation key in dot notation (e.g., 'home.title')
 * @param vars - Optional placeholders {{name}} in the translation string
 */
export function t(key: string, vars?: Record<string, string | number>): string {
  const language = currentLanguage;
  const langTranslations = translations[language] || translations.en;

  let value = langTranslations[key];

  if (!value && language !== 'en') {
    value = translations.en[key];
  }

  const resolved = value || key;
  if (vars && value) {
    return interpolate(resolved, vars);
  }
  return resolved;
}

/**
 * Check if current language is RTL (right-to-left)
 */
export function isRTL(): boolean {
  // Currently no RTL languages, but can be extended
  return false;
}

/**
 * Get language name from code
 */
export function getLanguageName(code: Language): string {
  return code === 'zh' ? '中文' : 'English';
}

/**
 * Get all available languages
 */
export function getAvailableLanguages(): Array<{ code: Language; name: string }> {
  return [
    { code: 'en', name: 'English' },
    { code: 'zh', name: '中文' },
  ];
}
