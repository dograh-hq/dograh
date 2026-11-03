- While we are playing the initial greeting, we would listen to what the other end is playing and determine
    - Whether its a voicemail or call screening or a person
        - Voicemail -> Leave a pre configured message and drop the call
        - Call screening -> say the screening message - like (I am xxx calling for yyy) - which can be confugured once the screener has played out its message (typically - if you tell me your name and reason for calling, i will see if thie person is available) is done . After we play out a pre configured announcement, the phone actually rings and the user answers the phone. So, we should ideally not speak anything unless the user has answered the phone
        - Conversation -> A person actually answers phone

- Other subtleties
    - The person can actually pick up their phone in between voicemail or screener message - so we have to handle that
    - The configuration will go in workflow settings where we have a basic voicemail prompt. We will remove the prompt from the UI and put the message that the agent should leave once they determine its voicemail, and also provide an option to leave the screener message
