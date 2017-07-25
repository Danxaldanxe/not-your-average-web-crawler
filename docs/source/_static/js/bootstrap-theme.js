function selectText(containerid) {
    if (document.selection) {
        var range = document.body.createTextRange();
        range.moveToElementText(document.getElementById(containerid));
        range.select();
    } else if (window.getSelection) {
        var range = document.createRange();
        range.selectNode(document.getElementById(containerid));
        window.getSelection().removeAllRanges();
        window.getSelection().addRange(range);
    }
}

function unwrapToc() {
    $('.nav-stacked').each(function() {
        $(this).html($(this).find('ul').html());
    });
}

function tocInitializeAffix() {
    $('#tocscroll .nav').affix({
        offset: {
            top: function () {
                var alertHeight = 0;
                if ($('.alert-version').lenght) {
                    alertHeight = $('.alert-version').outerHeight()
                }

                return (this.top = $('.jumbotron').outerHeight() + alertHeight)
            },
            bottom: function () {
                return (this.bottom = $('.footer').outerHeight())
            }
        }
    });
}

function tocAffixSetWidth() {
    $('#tocscroll .nav').width($('#tocscroll').width())
}

$(document).ready(function() {
    unwrapToc();
    tocInitializeAffix();
    tocAffixSetWidth();
});

$(window).resize(function () {
    tocAffixSetWidth();
});
