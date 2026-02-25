from django.core.mail import EmailMessage


def send(title, content, to, cc, files):
    email = EmailMessage(
        title,
        content,
        to=to,
        cc=cc
    )

    for filename, data, content_type in files:
        email.attach(filename, data, content_type)

    email.send()
