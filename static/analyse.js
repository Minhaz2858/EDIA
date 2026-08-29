var back = 'The result is good!'; // Stores the response from the backend

var candidates = ['', '', '', '', '', '', '', '', '', '', '', '', '', '', '', '', '', '']; // Stores recommended comments

var click_record = ''; // To store the click event for recommendation navigation (0 - previous, 1 - next).

var url_base = 'http://127.0.0.1:5000'; // Base URL for the Flask backend API.

var current_mode_button_id = ''; // Keeps track of which preview button ('preview_btn' or 'recommend_btn') was last clicked.

var questionNumber = 0; // Tracks the current original post/question being displayed. -1 typically means initial state.

// Defines the order in which original posts (OP) should be displayed.
// UPDATED: New indices for your 6 posts (0-5) followed by the "Thank you" post (index 6).
var op_post_order = [0, 1, 2, 3, 4, 5, 6];

var initial_comment = ''; // Stores the user's comment before any preview/submission.
var submitted_comment = ''; // Stores the user's comment after final submission.

var first_click = true; // Flag to determine if it's the very first interaction with the comment box.


// Introductory messages for the MepsBot, displayed in the sidebar.
intro_0 = 'Hi, <mark>BepsBot</mark> here!';
intro_1 = '<b>I can help check your comment and give you some feedbacks. I will securely protect your data.</b> <br> It is important to show <b>informational (e.g., advice, knowledge)</b> and <b>emotional (e.g., understanding, encouragement)</b> support in your comment. ';
intro_2 = '<b>I can recommend some good comments that could be similar to your current one. I will securely protect your data.</b> <br> It is important to show <b>informational (e.g., advice, knowledge)</b> or <b>emotional (e.g., understanding, encouragement) support</b> in your comment.';


var cur_ind = [0, 1, 2] // Current indices for displaying recommendation candidates (shows 3 at a time).


/**
 * Sends a POST request to the specified backend endpoint and handles the response.
 * This function dynamically calls either the assessment or recommendation backend
 * based on the provided endpoint and manages UI state.
 * @param {string} endpoint - The backend API endpoint (e.g., '/assess' or '/recommend').
 * @param {string} buttonId - The ID of the button that triggered this function ('preview_btn' or 'recommend_btn').
 */
function getPy(endpoint, buttonId) {
    if (questionNumber === 6) return 0;
    var sendVar = document.getElementById("reply").value;
    // If the reply box is empty, do nothing and exit the function.
    if (sendVar == '') { return 0; }




    // Get references to key UI elements
    const previewBtn = document.getElementById('preview_btn');
    const recommendBtn = document.getElementById('recommend_btn');
    const replyBox = document.getElementById('reply');
    const submitBtn = document.getElementById('submit_btn');


    // Check if the clicked button is currently in its "Preview" state (meaning the user wants to initiate a preview)
    if (document.getElementById(buttonId).innerHTML.includes('Preview')) {
        // Store the ID of the button that was clicked to remember the active preview mode (Assessment or Recommendation)
        current_mode_button_id = buttonId;


        // Clear previous results displayed in the sidebar, but keep the user's comment in the reply box.
        refresh(true);


        // Change the text of the clicked button to "Quit".
        document.getElementById(buttonId).innerHTML = 'Quit';


        // --- NEW LOGIC FOR OTHER BUTTON VISIBILITY ---
        if (buttonId === 'preview_btn') {
            // If 'Preview Assessment' was clicked, 'Preview Recommendation' stays visible
            recommendBtn.style.display = '';
        } else { // buttonId === 'recommend_btn'
            // If 'Preview Recommendation' was clicked, 'Preview Assessment' should be hidden
            previewBtn.style.display = 'none';
        }
        // --- END NEW LOGIC ---


        // Disable the reply input box to prevent further editing during preview.
        replyBox.disabled = false;
        // Show the "Continue to submit" button.
        submitBtn.style.display = "";


    } else { // This block runs if the clicked button is already "Quit" (meaning the user wants to return to edit mode)
        // Reset the clicked button's text back to its original "Preview" state.
        document.getElementById(buttonId).innerHTML = (buttonId === 'preview_btn' ? 'Preview Assessment' : 'Preview Recommendation');


        // Enable the reply input box.
        replyBox.disabled = false;
        // Hide the "Continue to submit" button.
        submitBtn.style.display = "None";


        // Ensure both preview buttons are visible and their text is reset to their default "Preview" state.
        previewBtn.style.display = '';
        recommendBtn.style.display = '';
        previewBtn.innerHTML = 'Preview Assessment'; // Explicitly reset text for consistency
        recommendBtn.innerHTML = 'Preview Recommendation'; // Explicitly reset text for consistency


        // Clear all results in the sidebar AND clear the reply box.
        refresh(false);
        // Exit the function as the UI has been reset.
        return 0;
    }


    // Prepare data to send to the backend, including the user's comment and click record.
    // var with_record = sendVar + 'click event:' + click_record;
    var http = new XMLHttpRequest(); // Create a new XMLHttpRequest object for AJAX.
    http.open('POST', url_base + endpoint, true); // Configure the request (POST, URL, asynchronous).
    // Set the request header for JSON data.
    http.setRequestHeader('Content-type', 'application/json');

    // Get OP Text
    var op_text = "";
    var opContentElem = document.getElementById("OP-content");
    if (opContentElem) {
        op_text = opContentElem.innerText;
    }

    var payload = {
        comment: sendVar,
        click_event: click_record,
        op_text: op_text
    };

    // Define the callback function to handle the response from the backend.
    http.onreadystatechange = function () {
        // Check if the request is complete (readyState 4) and successful (status 200).
        if (http.readyState == 4 && http.status == 200) {
            var result = http.responseText;
            back = JSON.parse(result); // Parse the JSON response from the backend.


            // Display results based on the 'mode' received from the backend.
            if (back.mode == 'AF') { // If mode is 'AF' (Assessment Feedback)
                var IS = 'Medium';
                var ES = 'Medium';
                // Map numerical scores to descriptive labels (Low, Medium, High).
                if (back.IS_score == 1) { IS = 'Low'; } else if (back.IS_score == 3) { IS = 'High'; }
                if (back.ES_score == 1) { ES = 'Low'; } else if (back.ES_score == 3) { ES = 'High'; }


                var feedback_1 = back.feedback_1; // Get the first feedback message.
                var feedback_2 = back.feedback_2; // Get the second feedback/suggestion message.
                // Construct the full score and intro script for display.
                var score_script = intro_1 + '<br><br><b>Report on your comment:</b> <br>' + 'Informational Support: ' + '<mark>' + IS + '</mark>' + '<br>'
                    + 'Emotional Support: ' + '<mark>' + ES + '</mark>' + '<br>';


                // Update UI elements for assessment feedback.
                document.getElementById("title_box").style.display = ""; // Show the title box.
                document.getElementById("intro").innerHTML = score_script; // Set the intro text with scores.
                document.getElementById("feedback_box").style.display = ""; // Show the feedback box.
                document.getElementById("feedback_1").innerHTML = feedback_1; // Set the first feedback.
                document.getElementById("suggestion").innerHTML = feedback_2; // Set the suggestion.
                // The submit_btn is already visible from the initial 'if' block.
            }
            else if (back.mode == 'RE') { // If mode is 'RE' (Recommendation)
                // Populate the candidates array with recommended comments from the backend.
                for (i = 0; i < candidates.length; i++) {
                    var index = i.toString()
                    candidates[i] = back[index]
                }
                // Construct the recommendation feedback script with color-coded word explanations.
                //  var re_feedback_script = intro_2 + '<br><br>' + back.feedback + '<br> <font color=blue>blue word</font> - personal pronouns <br> <mark><font color=green>green word</font></mark> - about family and friend <br> <mark><font color=red>red word</font></mark> - positive word';
                var re_feedback_script = intro_2 + '<br><br>' + back.feedback + '<br>- personal pronouns <br>- about family and friend <br>- positive word';


                // Update UI elements for recommendations.
                document.getElementById("title_box").style.display = ""; // Show the title box.
                document.getElementById("intro").innerHTML = re_feedback_script; // Set the intro text with recommendation feedback.
                document.getElementById("pre_rec").style.display = ""; // Show "Previous" recommendation button.
                document.getElementById("next_rec").style.display = ""; // Show "More" recommendation button.


                document.getElementById("help_box_1").style.display = ""; // Show first recommendation box.
                // Display the first recommended comment and its description
                let desc0 = back[cur_ind[0] + '_description'] ? '<br><i>' + back[cur_ind[0] + '_description'] + '</i>' : '';
                document.getElementById("rec_1").innerHTML = candidates[cur_ind[0]] + desc0;


                document.getElementById("help_box_2").style.display = ""; // Show second recommendation box.
                // Display the second recommended comment and its description
                let desc1 = back[cur_ind[1] + '_description'] ? '<br><i>' + back[cur_ind[1] + '_description'] + '</i>' : '';
                document.getElementById("rec_2").innerHTML = candidates[cur_ind[1]] + desc1;


                document.getElementById("help_box_3").style.display = ""; // Show third recommendation box.
                // Display the third recommended comment and its description
                let desc2 = back[cur_ind[2] + '_description'] ? '<br><i>' + back[cur_ind[2] + '_description'] + '</i>' : '';
                document.getElementById("rec_3").innerHTML = candidates[cur_ind[2]] + desc2;
                // The submit_btn is already visible from the initial 'if' block.
            }


            // If it's the first interaction, store the initial comment.
            if (first_click == true) {
                initial_comment = sendVar;
                first_click = false;
            }
        }
    };
    http.send(JSON.stringify(payload)); // Send the request to the backend.
}


function previewAssessment() {
    getPy('/assess', 'preview_btn');
}



function previewRecommendation() {
    getPy('/recommend', 'recommend_btn');
}


/**
 * Replaces the content of the main comment input box with the selected recommendation.
 * @param {number} recIndex - The index (0, 1, or 2) of the recommendation to accept.
 */
function acceptRecommendation(recIndex) {
    // Calculate the actual index in the `candidates` array, considering pagination
    // `cur_ind[0]` holds the starting index of the currently displayed recommendations.
    const actualCandidateIndex = cur_ind[0] + recIndex;
    if (actualCandidateIndex < candidates.length) {
        document.getElementById('reply').value = candidates[actualCandidateIndex];
    }
}



op_title = [
    "I feel as if I am not permitted to have any feelings.",
    "How do you handle ruminating or having too many thoughts?",
    "Having the feeling of being a fake",
    "Thank you for our time" // This is the final "Thank you" message
];


op_content = [
    // "My doctoral program in the humanities is really rigorous, and I am a 30-year-old candidate. My long-term mental health condition has been under my careful private management for a number of years. Because of bad experiences in the past and the dread of criticism from family, I've learnt to hide it. There is a lot of pressure to seem strong, productive, and in control here at school. I feel like I'm putting on an act a lot of the time, appearing happy and self-assured in front of other people while secretly fighting against crippling emotions of loneliness and inadequacy. <br><br> My anxiety of being \"too much\" for my therapist persists even though I am seeing a professional for treatment through medication and frequent therapy. In the time between sessions, I'm afraid that if I tell you my worst fears, you might drop me. As I listen to my classmates talk about \"grad school stress\" on the surface, I often feel alone in my struggles with a deep despair that is distinct from theirs. My idea that seeking assistance is a manifestation of weakness is deeply rooted in my past experiences. <br><br> Given my anxieties and experiences, I'm curious about where I could get real assistance, both within and outside of my academic community.",
    "Disclosure of my bipolar disorder is something I deeply regret. I realize it's unfair to assume it would alter my feelings, but perhaps if I hadn't brought it up, I would have felt more entitled and right in expressing my sentiments. <br><br> No matter how legitimate my worries are, the way I voice them usually leads to them being dismissed or even called insane. After enduring postpartum psychosis for so long, I no longer have faith in my own views, which makes it difficult for me to be honest with my spouse about my feelings. Instead of being this unpredictable, challenging person, I would prefer to simply be numb. <br><br> I have a hard time communicating my needs and dealing with disagreement. My temper flares up easily, and I often find myself yelling, which exacerbates the situation. With my manic and depressive episodes, I struggle to keep up with my responsibilities at home, in the classroom, and on the job. It appears like everyone ignores me, and I'm always exhausted, overwhelmed, and annoyed. <br><br> I would greatly appreciate it if you will listen to me and support me. I would like it if not everything was written off as a \"bipolar issue\" or a side effect of the drug. Being alone is the worst. No one takes me seriously, I can't seem to get to the bottom of things, and my communication skills are very lacking.",
    "Hello everyone, <br> Thank goodness, things have been going relatively nicely for me lately. Things have been really calm for the past few months, and I haven't had any big problems, which is a wonderful relief. <br><br> But even when things are peaceful, I'm still having a hard time with overthinking. I often find myself going over discussions in my thoughts, especially with some folks. This quickly turns into a lot of self-hatred and even ideas of hurting myself. It's been going on for months now, and it's really hurting my mental health. <br><br> I'm curious whether a lot of people with bipolar disorder have this kind of rumination. How do you deal with this if you've been through it? Any tips or ideas that have worked for you would be greatly appreciated. <br> Thanks for listening.",
    "My therapist recently recommended that I undergo testing for Type II bipolar illness after doing an evaluation. He will suggest that I see a psychiatrist in order to get an accurate diagnosis. <br><br> It was a relief, in a way; now maybe all this stuff I've been confused about makes sense. The problem is that I'm starting to panic now. A severe case of imposter syndrome has set established. Every time I go to the psychiatrist, I imagine them telling me I'm just being dramatic or that they're wasting their time since I managed to sneak up on my therapist. <br><br> Does this even exist? I can't stop wondering if I'm \"sick enough\" to make it so. At the moment, I am utterly confused and feel like a complete fraud. <br><br> If anyone knows what I'm going through or has any suggestions, please share... Hearing it would mean a lot to me. Was this how you felt before you got your diagnosis, too?",
    "Really appreciate your participation." // This is the final "Thank you" message content
];


op_author_name = [
    'UserA',
    'UserB',
    'UserC',
    'UserD',
    'UserE',
    'UserF',
    'Harry' // Author for the final "Thank you" post
];



function changeQuestion() {
    console.log(op_author_name[op_post_order[questionNumber]]);
    document.getElementById("OP-name").innerHTML = op_author_name[op_post_order[questionNumber]];
    document.getElementById("OP-title").innerHTML = op_title[op_post_order[questionNumber]];
    document.getElementById("OP-content").innerHTML = op_content[op_post_order[questionNumber]];


    // If it's the thank you page (index 6)
    if (questionNumber === 6) {
        // Disable the reply box
        document.getElementById("reply").disabled = true;
        document.getElementById("reply").value = "Thank you for participating!";
        document.getElementById("reply").style.backgroundColor = "#f5f5f5";


        // Hide all buttons
        document.getElementById("preview_btn").style.display = "none";
        document.getElementById("recommend_btn").style.display = "none";
        document.getElementById("submit_btn").style.display = "none";


        // Clear any active feedback
        refresh(false);
    }
}


/**
 * Resets the UI elements to their initial hidden state.
 * @param {boolean} isPreview - If true, the reply textarea will not be cleared, preserving the user's input during preview.
 */
function refresh(isPreview = false) {
    // Hide various sidebar elements and buttons.
    document.getElementById("submit_btn").style.display = "None";
    document.getElementById("title_box").style.display = "None";
    document.getElementById("assess_box").style.display = "None";
    document.getElementById("feedback_box").style.display = "None";
    document.getElementById("pre_rec").style.display = "None";
    document.getElementById("next_rec").style.display = "None";
    document.getElementById("help_box_1").style.display = "None";
    document.getElementById("help_box_2").style.display = "None";
    document.getElementById("help_box_3").style.display = "None";
    document.getElementById("re_feedback_box").style.display = "None";


    // Only clear the reply box if not in a preview state (i.e., when returning from "Quit" or initial page load).
    if (!isPreview) {
        document.getElementById("reply").value = ''; // Clear the text area.
        document.getElementById("reply").placeholder = 'What are your thoughts?'; // Reset placeholder text.
    }
}


/**
 * Handles the final submission of the user's comment.
 * It determines the backend endpoint based on the last active preview mode.
 */
function submit_one() {
    if (questionNumber === 6) return;
    document.getElementById("reply").disabled = false;
    // Re-enable the reply box.


    // Reset the text of both preview buttons to their default state.
    document.getElementById('preview_btn').innerHTML = 'Preview Assessment';
    document.getElementById('recommend_btn').innerHTML = 'Preview Recommendation';


    // Ensure both preview buttons are visible after submission.
    document.getElementById('preview_btn').style.display = '';
    document.getElementById('recommend_btn').style.display = '';


    // Hide the "Continue to submit" button after submission.
    document.getElementById("submit_btn").style.display = "None";


    // Handle the initial state where no question has been loaded yet.
    if (questionNumber == -1) {
        refresh(); // Clear UI.
        questionNumber++; // Move to the first question.
        changeQuestion(); // Display the first question.
        first_click = true; // Reset first click flag.
        // UPDATED: Changed the condition to reflect the new number of questions (6 actual posts + 1 thank you = 7 total)
    } else if (questionNumber < op_post_order.length - 1) { // Check if there are more questions before the "Thank you" post
        var sendVar = document.getElementById("reply").value; // Get the submitted comment.
        submitted_comment = sendVar; // Store the submitted comment.
        // var final_com = 'Yeah final' + sendVar + 'click event:' + click_record; // Prepare data for backend.
        var http = new XMLHttpRequest(); // Create new XMLHttpRequest.


        // Determine which backend endpoint to send the final submission to based on the last preview mode.
        let submitEndpoint = '/assess'; // Default to assess if no preview was made.
        if (current_mode_button_id === 'recommend_btn') {
            submitEndpoint = '/recommend'; // If recommendation was last active, submit to recommendation endpoint.
        }


        http.open('POST', url_base + submitEndpoint, true); // Configure the request.
        http.setRequestHeader('Content-type', 'application/json'); // Set header.

        // Get OP Text
        var op_text = "";
        var opContentElem = document.getElementById("OP-content");
        if (opContentElem) {
            op_text = opContentElem.innerText;
        }

        var payload = {
            comment: sendVar,
            click_event: click_record,
            op_text: op_text,
            is_final: true
        };

        // Callback for backend response.
        http.onreadystatechange = function () {
            if (http.readyState == 4 && http.status == 200) {
                var result = http.responseText;
                back = JSON.parse(result); // Parse response.
                refresh(); // Clear UI after submission.
                click_record = ''; // Reset click record.
                questionNumber++; // Move to the next question.
                changeQuestion(); // Display the next question.
                first_click = true; // Reset first click flag for the new question.
            }
        };
        http.send(JSON.stringify(payload)); // Send the final submission to the backend.
    } else {
        // If all questions are completed, display a thank you message.
        refresh(); // Clear UI.
        questionNumber++; // Increment to point to the "Thank you" post
        document.getElementById("OP-name").innerHTML = op_author_name[op_post_order[questionNumber]]; // Use the 'Harry' author for the thank you
        document.getElementById("OP-title").innerHTML = op_title[op_post_order[questionNumber]]; // Use the "Thank you" title
        document.getElementById("OP-content").innerHTML = op_content[op_post_order[questionNumber]]; // Use the "Thank you" content
        document.getElementById("reply").value = 'Thank you! You have finished all the tasks.'; // Set a thank you message in the reply box.
    }
}




/**
 * Advances to the next task/question.
 * This is likely used for a "Start next task" button in the survey section.
 */
function next_task() {
    questionNumber++; // Increment question number.
    // UPDATED: Changed the condition to reflect the new number of questions (6 actual posts + 1 thank you = 7 total)
    if (questionNumber < op_post_order.length) {
        changeQuestion(); // Display the new question.
        first_click = true; // Reset first click flag.
    }


    // If all questions are completed, display a thank you message.
    if (questionNumber == op_post_order.length - 1) { // Adjusted to match the index of the "Thank you" post
        refresh();
        document.getElementById("OP-name").innerHTML = op_author_name[op_post_order[questionNumber]];
        document.getElementById("OP-title").innerHTML = op_title[op_post_order[questionNumber]];
        document.getElementById("OP-content").innerHTML = op_content[op_post_order[questionNumber]];
        document.getElementById("reply").value = 'Thank you! You have finished all the tasks.'; // Set a thank you message in the reply box.
    }
}


/**
 * Navigates to the previous set of recommended comments.
 * Updates cur_ind to show the previous 3 candidates.
 */
function pre_rec() {
    // Prevent going before the first set of recommendations.
    if (cur_ind[0] == 0) { /* do nothing */ }
    else {
        // Decrement indices to show the previous 3 comments.
        cur_ind[0] = cur_ind[0] - 3;
        cur_ind[1] = cur_ind[1] - 3;
        cur_ind[2] = cur_ind[2] - 3;
        click_record = click_record + '0 '; // Record the "previous" click.
    }
    // Update the displayed recommended comments and their descriptions.
    let desc0 = back[cur_ind[0] + '_description'] ? '<br><i>' + back[cur_ind[0] + '_description'] + '</i>' : '';
    document.getElementById("rec_1").innerHTML = candidates[cur_ind[0]] + desc0;


    let desc1 = back[cur_ind[1] + '_description'] ? '<br><i>' + back[cur_ind[1] + '_description'] + '</i>' : '';
    document.getElementById("rec_2").innerHTML = candidates[cur_ind[1]] + desc1;


    let desc2 = back[cur_ind[2] + '_description'] ? '<br><i>' + back[cur_ind[2] + '_description'] + '</i>' : '';
    document.getElementById("rec_3").innerHTML = candidates[cur_ind[2]] + desc2;
}


/**
 * Navigates to the next set of recommended comments.
 * Updates cur_ind to show the next 3 candidates.
 */
function next_rec() {
    // Prevent going beyond the last set of recommendations (assuming 18 candidates, 3 per page).
    if (cur_ind[2] == 17) { /* do nothing */ }
    else {
        // Increment indices to show the next 3 comments.
        cur_ind[0] = cur_ind[0] + 3;
        cur_ind[1] = cur_ind[1] + 3;
        cur_ind[2] = cur_ind[2] + 3;
        click_record = click_record + '1 '; // Record the "next" click.
        // Update the displayed recommended comments and their descriptions.
        let desc0 = back[cur_ind[0] + '_description'] ? '<br><i>' + back[cur_ind[0] + '_description'] + '</i>' : '';
        document.getElementById("rec_1").innerHTML = candidates[cur_ind[0]] + desc0;


        let desc1 = back[cur_ind[1] + '_description'] ? '<br><i>' + back[cur_ind[1] + '_description'] + '</i>' : '';
        document.getElementById("rec_2").innerHTML = candidates[cur_ind[1]] + desc1;


        let desc2 = back[cur_ind[2] + '_description'] ? '<br><i>' + back[cur_ind[2] + '_description'] + '</i>' : '';
        document.getElementById("rec_3").innerHTML = candidates[cur_ind[2]] + desc2;
    }
}


/**
 * Deactivates the 'active' class from all pagination numbers.
 * This is called before setting a new active page.
 */
function deactiveNumber() {
    document.getElementById('tp1').className = "";
    document.getElementById('tp2').className = "";
    document.getElementById('tp3').className = "";
    document.getElementById('tp4').className = "";
    document.getElementById('tp5').className = "";
    document.getElementById('tp6').className = "";
    document.getElementById('tp7').className = "";
    document.getElementById('tp8').className = "";
    document.getElementById('tp9').className = "";
    document.getElementById('tp10').className = "";
}


/**
 * Changes the displayed sample question/page based on the clicked pagination ID.
 * @param {string} id - The ID of the clicked pagination button (e.g., 'bp1', 'bp2').
 */
function changePage(id) {
    // Get references to all page elements.
    var page1 = document.getElementById('Page1');
    var page2 = document.getElementById('Page2');
    var page3 = document.getElementById('Page3');
    var page4 = document.getElementById('Page4');
    var page5 = document.getElementById('Page5');
    var page6 = document.getElementById('Page6');
    var page7 = document.getElementById('Page7');
    var page8 = document.getElementById('Page8');
    var page9 = document.getElementById('Page9');
    var page10 = document.getElementById('Page10');
    // Create an array of page elements for easier iteration.
    var pageList = new Array(page1, page2, page3, page4, page5, page6, page7, page8, page9, page10);


    deactiveNumber(); // Deactivate any currently active pagination number.


    // Use a switch statement to handle different page IDs.
    switch (id) {
        case "bp1":
            for (var item in pageList) {
                if (pageList[item] == page1) { // If it's the target page (Page1)
                    pageList[item].style = "display: inline"; // Show it.
                    pageList[item].className = "col-lg-8 col-md-8"; // Apply Bootstrap column classes.
                    document.getElementById(id).childNodes[0].className = "active"; // Set the pagination number as active.
                }
                else { // For all other pages
                    pageList[item].style = "display: none"; // Hide them.
                    pageList[item].className = "col-lg-8 col-md-8"; // Keep column classes.
                }
            }
            break;
        case "bp2":
            for (var item in pageList) {
                if (pageList[item] == page2) {
                    pageList[item].style = "display: inline";
                    pageList[item].className = "col-lg-8 col-md-8";
                    document.getElementById(id).childNodes[0].className = "active";
                }
                else {
                    pageList[item].style = "display: none";
                    pageList[item].className = "col-lg-8 col-md-8";
                }
            }
            break;
        case "bp3":
            for (var item in pageList) {
                if (pageList[item] == page3) {
                    pageList[item].style = "display: inline";
                    pageList[item].className = "col-lg-8 col-md-8";
                    document.getElementById(id).childNodes[0].className = "active";
                }
                else {
                    pageList[item].style = "display: none";
                    pageList[item].className = "col-lg-8 col-md-8";
                }
            }
            break;
        case "bp4":
            for (var item in pageList) {
                if (pageList[item] == page4) {
                    pageList[item].style = "display: inline";
                    pageList[item].className = "col-lg-8 col-md-8";
                    document.getElementById(id).childNodes[0].className = "active";
                }
                else {
                    pageList[item].style = "display: none";
                    pageList[item].className = "col-lg-8 col-md-8";
                }
            }
            break;
        case "bp5":
            for (var item in pageList) {
                if (pageList[item] == page5) {
                    pageList[item].style = "display: inline";
                    pageList[item].className = "col-lg-8 col-md-8";
                    document.getElementById(id).childNodes[0].className = "active";
                }
                else {
                    pageList[item].style = "display: none";
                    pageList[item].className = "col-lg-8 col-md-8";
                }
            }
            break;
        case "bp6":
            for (var item in pageList) {
                if (pageList[item] == page6) {
                    pageList[item].style = "display: inline";
                    pageList[item].className = "col-lg-8 col-md-8";
                    document.getElementById(id).childNodes[0].className = "active";
                }
                else {
                    pageList[item].style = "display: none";
                    pageList[item].className = "col-lg-8 col-md-8";
                }
            }
            break;
        case "bp7":
            for (var item in pageList) {
                if (pageList[item] == page7) {
                    pageList[item].style = "display: inline";
                    pageList[item].className = "col-lg-8 col-md-8";
                    document.getElementById(id).childNodes[0].className = "active";
                }
                else {
                    pageList[item].style = "display: none";
                    pageList[item].className = "col-lg-8 col-md-8";
                    document.getElementById(id).childNodes[0].className = "active"; // This line is redundant if already handled above.
                }
            }
            break;
        case "bp8":
            for (var item in pageList) {
                if (pageList[item] == page8) {
                    pageList[item].style = "display: inline";
                    pageList[item].className = "col-lg-8 col-md-8";
                    document.getElementById(id).childNodes[0].className = "active";
                }
                else {
                    pageList[item].style = "display: none";
                    pageList[item].className = "col-lg-8 col-md-8";
                }
            }
            break;
        case "bp9":
            for (var item in pageList) {
                if (pageList[item] == page9) {
                    pageList[item].style = "display: inline";
                    pageList[item].className = "col-lg-8 col-md-8";
                    document.getElementById(id).childNodes[0].className = "active";
                }
                else {
                    pageList[item].style = "display: none";
                    pageList[item].className = "col-lg-8 col-md-8";
                }
            }
            break;
        case "bp10":
            for (var item in pageList) {
                if (pageList[item] == page10) {
                    pageList[item].style = "display: inline";
                    pageList[item].className = "col-lg-8 col-md-8";
                    document.getElementById(id).childNodes[0].className = "active";
                }
                else {
                    pageList[item].style = "display: none";
                    pageList[item].className = "col-lg-8 col-md-8";
                }
            }
            break;
    }
}

