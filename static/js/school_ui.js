/* Small local UI helpers; submissions and server endpoints are left to existing scripts. */
(function () {
    'use strict';
    document.addEventListener('DOMContentLoaded', function () {
        var taskTools = document.querySelector('.assignment-detail-sticky-tools');
        if (taskTools) {
            var root = document.documentElement;
            root.classList.add('school-has-task-tools');
            var navbar = document.querySelector('.navbar');
            function updateTaskOffsets() {
                var scale = parseFloat(getComputedStyle(root).zoom) || 1;
                var navHeight = navbar ? navbar.getBoundingClientRect().height / scale : 0;
                var navTop = navbar ? parseFloat(getComputedStyle(navbar).top) || 0 : 0;
                root.style.setProperty('--school-sticky-top', Math.ceil(navHeight + navTop) + 'px');
                root.style.setProperty('--school-tools-height', Math.ceil(taskTools.getBoundingClientRect().height / scale) + 'px');
            }
            updateTaskOffsets();
            if (window.ResizeObserver) {
                var taskObserver = new ResizeObserver(updateTaskOffsets);
                taskObserver.observe(taskTools);
                if (navbar) taskObserver.observe(navbar);
            }
            window.addEventListener('resize', updateTaskOffsets);
            new MutationObserver(updateTaskOffsets).observe(root, {attributes:true, attributeFilter:['data-font-scale']});

            var jumpsNav = taskTools.querySelector('.assignment-detail-jumps');
            if (jumpsNav) {
                var jumpLinks = jumpsNav.querySelectorAll('a[href^="#"]');
                var isUserClicking = false;
                var clickTimeout = null;

                var setActiveJump = function (linkOrHash) {
                    var activeLink = null;
                    if (typeof linkOrHash === 'string') {
                        for (var i = 0; i < jumpLinks.length; i++) {
                            if (jumpLinks[i].getAttribute('href') === linkOrHash) {
                                activeLink = jumpLinks[i];
                                break;
                            }
                        }
                    } else {
                        activeLink = linkOrHash;
                    }
                    if (!activeLink) return;

                    jumpLinks.forEach(function (l) {
                        l.classList.remove('is-active');
                        l.removeAttribute('aria-current');
                    });
                    activeLink.classList.add('is-active');
                    activeLink.setAttribute('aria-current', 'true');
                };

                var highlightSection = function (targetEl) {
                    if (!targetEl) return;
                    targetEl.classList.remove('section-attention-highlight');
                    void targetEl.offsetWidth;
                    targetEl.classList.add('section-attention-highlight');

                    var onAnimEnd = function () {
                        targetEl.classList.remove('section-attention-highlight');
                        targetEl.removeEventListener('animationend', onAnimEnd);
                    };
                    targetEl.addEventListener('animationend', onAnimEnd);
                    setTimeout(onAnimEnd, 1400);
                };

                jumpLinks.forEach(function (link) {
                    link.addEventListener('click', function (event) {
                        var hash = link.getAttribute('href');
                        if (!hash || hash.charAt(0) !== '#') return;

                        setActiveJump(link);

                        if (link.id === 'assignment-help-button') {
                            var helpCard = document.getElementById('assignment-help-card');
                            if (helpCard) highlightSection(helpCard);
                            return;
                        }

                        var targetEl = document.querySelector(hash);
                        if (targetEl) {
                            event.preventDefault();
                            isUserClicking = true;
                            clearTimeout(clickTimeout);
                            clickTimeout = setTimeout(function () {
                                isUserClicking = false;
                            }, 800);

                            if (history.pushState) {
                                history.pushState(null, '', hash);
                            } else {
                                location.hash = hash;
                            }

                            var isReduced = window.SchoolNetPerformance && window.SchoolNetPerformance.isMotionReduced();
                            targetEl.scrollIntoView({
                                behavior: isReduced ? 'auto' : 'smooth',
                                block: 'start'
                            });

                            highlightSection(targetEl);
                        }
                    });
                });

                if ('IntersectionObserver' in window) {
                    var sectionIds = [];
                    jumpLinks.forEach(function (l) {
                        var h = l.getAttribute('href');
                        if (h && h.charAt(0) === '#' && h !== '#assignment-help-card') {
                            sectionIds.push(h.substring(1));
                        }
                    });
                    var sections = sectionIds.map(function (id) {
                        return document.getElementById(id);
                    }).filter(Boolean);

                    if (sections.length > 0) {
                        var observer = new IntersectionObserver(function (entries) {
                            if (isUserClicking) return;
                            var visibleEntries = entries.filter(function (e) { return e.isIntersecting; });
                            if (visibleEntries.length > 0) {
                                visibleEntries.sort(function (a, b) {
                                    return Math.abs(a.boundingClientRect.top) - Math.abs(b.boundingClientRect.top);
                                });
                                var topId = visibleEntries[0].target.id;
                                setActiveJump('#' + topId);
                            }
                        }, {
                            rootMargin: '-80px 0px -40% 0px',
                            threshold: [0, 0.25, 0.5, 0.75]
                        });

                        sections.forEach(function (sec) { observer.observe(sec); });
                    }
                }

                if (window.location.hash) {
                    var initTarget = document.querySelector(window.location.hash);
                    if (initTarget) {
                        setActiveJump(window.location.hash);
                        setTimeout(function () {
                            highlightSection(initTarget);
                        }, 250);
                    }
                }

                window.addEventListener('hashchange', function () {
                    if (window.location.hash) {
                        setActiveJump(window.location.hash);
                        var el = document.querySelector(window.location.hash);
                        if (el) highlightSection(el);
                    }
                });
            }
        }
        function closeMenus(event) {
            document.querySelectorAll('[data-school-menu]').forEach(function (menu) {
                if (!menu.open) return;
                if (event.type === 'keydown') {
                    if (event.key !== 'Escape') return;
                    menu.open = false;
                    menu.querySelector('summary').focus();
                } else if (!menu.contains(event.target) || event.target.closest('.school-card-menu-panel a, .school-card-menu-panel button')) menu.open = false;
            });
        }
        document.addEventListener('click', closeMenus);
        document.addEventListener('keydown', closeMenus);
        // The class filters are inline on small screens. Keep older guide links working.
        var legacyFilter = document.getElementById('mobile-menu-btn');
        if (legacyFilter && document.querySelector('.home-page')) {
            legacyFilter.addEventListener('click', function () {
                var filter = document.getElementById('main-sidebar');
                if (filter) {
                    filter.classList.remove('mobile-open');
                    document.body.classList.remove('sidebar-open');
                    filter.scrollIntoView({block:'start', behavior:'auto'});
                    var first = filter.querySelector('a');
                    if (first) first.focus({preventScroll:true});
                }
            });
        }
        // Bring validation failures into view without hiding server error messages.
        var submitForm = document.getElementById('submit-form');
        if (submitForm) {
            var alert = document.getElementById('form-validation-alert');
            if (alert) alert.setAttribute('role', 'alert');
            var drop = document.getElementById('file-drop-zone');
            if (drop) drop.addEventListener('keydown', function (event) {
                if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    drop.click();
                }
            });
        }
    });
}());
