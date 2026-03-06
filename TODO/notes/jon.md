A few thoughts:

I might be missing this, but are there consumption metrics included (number of times a Machine Learning model was utilized, number of times a dashboard was utilized, number of agent prompt calls)? If not, I think we want to have this. Most customers want this based on the amount of interest I get on this best practice from the SSDE program.
Tied to item above, I believe we should expand Pulse to pull in utilization data from outside of Dataiku for Dataiku created data artifacts (like datasets in a data lake(house)) so that customers can get the full picture of utilization of data products. Not sure if that would be agreed to from a leadership standpoint but I believe it helps our value proposition with customers.

I think it would be valuable to go a level deeper on the Usage Overview -> Development page to show the specific recipes used. I've gotten this question from a few major customers such as Apple. The aggregation into recipe categories is a good way to go. Just think we should give the ability to drill into the data at a recipe level.
FYI - I got the below error while looking through the Users page.
In general, the below metrics are the one's I recommend to customers. I've bolded the one's that I think should align with Pulse and italicized the ones that could align with Pulse depending on what Pulse's overall scope aims to be. It's likely too much to expect all of this to be included but this would be my wishlist.
ROAI/ROI - Govern value tracking integration
Number of users & active users
Number of users trained - Integrated with either in tool training or customer's training program data
Feature adoption
Net Promoter Score - Integrate with NPS survey data
Number of data products
Number of data artifacts by type
Data product cycle time
Business process adoption - With some external integrations outside of Dataiku to data storage query logs
Data product maturity
Metadata completeness - If customer is using Dataiku data catalog
Number of reusable datasets
Internal process/feature use