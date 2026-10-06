/* Apply before first paint; an explicit browser preference overrides hardware hints. */
(function () {
    'use strict';
    const key = 'sn-simplified-site';
    const root = document.documentElement;
    const motion = window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)') : null;
    const constrained = (navigator.hardwareConcurrency && navigator.hardwareConcurrency <= 4) ||
        (navigator.deviceMemory && navigator.deviceMemory <= 4);
    let preference;

    function readPreference() {
        try { return localStorage.getItem(key); } catch (error) { return undefined; }
    }
    function isSimplified() {
        return preference === 'on' || (preference !== 'off' && !!constrained);
    }
    function isMotionReduced() {
        return isSimplified() || !!(motion && motion.matches);
    }
    function update() {
        const simplified = isSimplified();
        const reduced = isMotionReduced();
        root.classList.toggle('sn-lite', simplified);
        root.classList.toggle('sn-reduced-motion', reduced);
        const policy = document.getElementById('navigation-transition-policy');
        if (policy) policy.textContent = reduced ? '@view-transition { navigation: none; }' :
            '@view-transition { navigation: auto; } @media (prefers-reduced-motion: reduce) { @view-transition { navigation: none; } }';
        const button = document.getElementById('simplified-site-toggle');
        if (button) {
            button.setAttribute('aria-checked', String(simplified));
            button.title = 'Спрощений сайт: ' + (simplified ? 'увімкнено' : 'вимкнено') +
                '. Натисніть, щоб ' + (simplified ? 'повернути декоративні ефекти.' : 'вимкнути анімації, розмиття й декоративні тіні.');
        }
        window.dispatchEvent(new CustomEvent('sn:appearance-change', {detail: {simplified, reducedMotion: reduced}}));
    }
    function setSimplified(enabled) {
        preference = enabled ? 'on' : 'off';
        try { localStorage.setItem(key, preference); } catch (error) { /* Still works for this page. */ }
        update();
    }
    window.SchoolNetPerformance = {
        isSimplified,
        isMotionReduced,
        scrollBehavior: function () { return isMotionReduced() ? 'instant' : 'smooth'; },
        setSimplified
    };
    preference = readPreference();
    update();
    if (motion) {
        if (motion.addEventListener) motion.addEventListener('change', update);
        else if (motion.addListener) motion.addListener(update);
    }
    window.addEventListener('storage', function (event) {
        if (event.key === key || event.key === null) {
            preference = readPreference();
            update();
        }
    });
    function updateVisibility() {
        root.classList.toggle('sn-page-hidden', document.hidden);
    }
    updateVisibility();
    document.addEventListener('visibilitychange', updateVisibility);
    // DOMContentLoaded and pageshow cover the first render and back/forward cache.
    document.addEventListener('DOMContentLoaded', function () {
        update();
        const button = document.getElementById('simplified-site-toggle');
        if (button) button.addEventListener('click', function () { setSimplified(!isSimplified()); });
        const navbar = document.querySelector('.navbar');
        if (navbar) {
            // Wrapped mobile actions must remain visible and the class menu starts below them.
            const updateNavbarHeight = function () {
                root.style.setProperty('--sn-navbar-height', navbar.getBoundingClientRect().height + 'px');
            };
            updateNavbarHeight();
            if (window.ResizeObserver) new ResizeObserver(updateNavbarHeight).observe(navbar);
            else window.addEventListener('resize', updateNavbarHeight);
        }
    });
    window.addEventListener('pageshow', function () {
        const saved = readPreference();
        // A blocked storage API must not discard the current page's selection.
        if (saved !== undefined) preference = saved;
        update();
        updateVisibility();
    });
}());
