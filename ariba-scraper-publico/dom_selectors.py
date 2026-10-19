SELECTORS = {
    # Login
    "username": 'input#userid',
    "username_button": 'a.w-login-page-form-btn',
    "password": 'input[id=Password]',
    "password_button": 'div#submitButton',

    #Popup al hacer login
    "ad_popup_close": 'div.target-lt-popupv2-close',
    "cookie_accept": 'button#truste-consent-button',

    # Listado de licitaciones
    "upper_menu": ".fd-shellbar__product button",
    "list_item": 'span[title="Ariba Proposals & Questionnaires"]',
    "buyers_dropdown": 'div.w-dashboard-tabs a.w-tabitem-a, a[bh="PML"], a.w-tabitem-a',
    "iframe": "iframe#SupplierFrame",  
    "active_proposals_link": 'div.w-page-tabs a, a:has-text("Propuestas"), a:has-text("Active Proposals"), a:has-text("Cuestionarios")',
    "table_rows": "tr.tableRow1, tr.tableRow2, tr[class*='tableRow']",
    "table_container": 'div.w-tab-content, div[_tbid], div.tableBody, div.awSearchTable',


    "detail_table": "table.tableBody",

    # En la página del evento
    "download_links": 'div.tableBody a.awmenuLink.hoverArrow',
    "link_popup": "div.awmenu:not(.is-dnone)"
}